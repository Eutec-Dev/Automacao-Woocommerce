# --------------------------- IMPORTAÇÕES ---------------------------
import requests
import pandas as pd
from woocommerce import API
from dotenv import load_dotenv
import os

# --------------------------- CONFIGURAÇÃO ---------------------------
load_dotenv()

# WooCommerce API
wcapi = API(
    url=os.getenv("https://eutec.com.br"),
    consumer_key=os.getenv("ck_5506e564a1f28a33558e9da73b33823db3c15510"),
    consumer_secret=os.getenv("cs_07393e037d36912181839d01905909d568448350"),
    version="wc/v3",
    timeout=10
)

# API Agis
API_URL = "https://vendas.agis.com.br/rest/all/V1/agis/reseller/product/list"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.getenv('1cnl71wepg3cqhu3t2nys2jgkks68yng')}"
}

# --------------------------- FUNÇÕES ---------------------------

def fetch_all_products_agis():
    """Busca todos os produtos da API Agis com paginação automática."""
    produtos = []
    pagina = 1
    page_size = 1000

    while True:
        params = {
            "searchCriteria[currentPage]": pagina,
            "searchCriteria[pageSize]": page_size
        }

        try:
            response = requests.get(API_URL, headers=HEADERS, params=params)

            # Verificação da resposta da API antes de processar
            if response.status_code != 200:
                print(f"Erro {response.status_code}: {response.text}")
                break

            data = response.json()

            # Se a resposta for válida, processamos os produtos
            if "items" in data and data["items"]:
                produtos.extend(data["items"])
                print(f"✅ Página {pagina} processada com {len(data['items'])} produtos.")

                # Verifica se há mais páginas para buscar
                if len(data["items"]) < page_size:
                    break  # Última página
                else:
                    pagina += 1
            else:
                print(f"🚨 Nenhum produto retornado na página {pagina}.")
                break

        except requests.exceptions.RequestException as e:
            print(f"Erro ao conectar à API Agis: {e}")
            break

    return pd.DataFrame(produtos)


def listar_produtos_woocommerce():
    """Lista produtos publicados com gerenciamento de estoque ativo."""
    produtos = []
    pagina = 1

    while True:
        response = wcapi.get("products", params={"per_page": 100, "page": pagina})

        if response.status_code != 200:
            print(f"Erro ao buscar produtos ({response.status_code}): {response.text}")
            return pd.DataFrame()

        try:
            dados = response.json()
        except requests.exceptions.JSONDecodeError:
            print("Erro: Resposta da API WooCommerce não é um JSON válido ou está vazia.")
            return pd.DataFrame()

        if not dados:
            break

        for prod in dados:
            if (
                prod.get("status") == "publish"
                and prod.get("manage_stock") is True
            ):
                produtos.append({
                    "SKU": prod.get("sku", ""),
                    "Preco": prod.get("price", "0.00"),
                    "Estoque": prod.get("stock_quantity", 0)
                })
        pagina += 1

    df = pd.DataFrame(produtos)

    if "SKU" not in df.columns:
        print("Erro: Coluna 'SKU' não encontrada no DataFrame WooCommerce.")
        return pd.DataFrame()

    df = df[df["Preco"] != "0.00"]
    df.dropna(subset=["Estoque"], inplace=True)

    df.columns = df.columns.str.strip()
    df["SKU"] = df["SKU"].astype(str)

    return df


def atualizar_produto(sku, novo_preco, novo_estoque):
    """Atualiza preço e estoque do produto via SKU."""
    response = wcapi.get("products", params={"sku": sku})

    if response.status_code == 200 and response.json():
        produto_id = response.json()[0]["id"]

        dados = {
            "regular_price": str(round(novo_preco, 2)),
            "stock_quantity": int(novo_estoque)
        }

        response = wcapi.put(f"products/{produto_id}", dados)
        if response.status_code == 200:
            print(f"✅ Atualizado: SKU {sku} | Preço R${novo_preco} | Estoque {novo_estoque}")
        else:
            print(f"❌ Erro ao atualizar SKU {sku}: {response.text}")
    else:
        print(f"Produto com SKU '{sku}' não encontrado.")


# --------------------------- ROTINA PRINCIPAL ---------------------------

if __name__ == "__main__":
    produtos_agis_df = fetch_all_products_agis()
    tabela_wc = listar_produtos_woocommerce()

    if not produtos_agis_df.empty and not tabela_wc.empty:
        produtos_agis_df.rename(columns={"sku": "SKU"}, inplace=True)

        print("✅ Dados da Agis e WooCommerce prontos para integração.")
        print("🔍 Iniciando merge das tabelas...")

        tabela_final = pd.merge(tabela_wc, produtos_agis_df, on="SKU", how="inner")

        for _, row in tabela_final.iterrows():
            atualizar_produto(
                sku=row["SKU"],
                novo_preco=row["price"],  # Ajuste para o campo correto da API Agis
                novo_estoque=row["stock"][0]["qty"] if "stock" in row and row["stock"] else 0
            )
    else:
        print("🚨 Um dos DataFrames está vazio, não foi possível realizar o merge.")
