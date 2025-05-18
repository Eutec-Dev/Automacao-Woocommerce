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
PARAMS = {
    "searchCriteria[currentPage]": 1,
    "searchCriteria[pageSize]": 1000
}

# --------------------------- FUNÇÕES ---------------------------

def listar_produtos_woocommerce():
    """Lista produtos publicados com gerenciamento de estoque ativo."""
    produtos = []
    pagina = 1

    while True:
        response = wcapi.get("products", params={"per_page": 100, "page": pagina})
        if response.status_code != 200:
            print("Erro ao buscar produtos:", response.text)
            break

        dados = response.json()
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
    df = df[df["Preco"] != "0.00"]
    df.dropna(subset=["Estoque"], inplace=True)
    
    # Garantindo que a coluna SKU está formatada corretamente
    df.columns = df.columns.str.strip()
    df["SKU"] = df["SKU"].astype(str)
    
    return df


def obter_id_por_sku(sku):
    """Retorna o ID do produto pelo SKU."""
    response = wcapi.get("products", params={"sku": sku})
    if response.status_code == 200 and response.json():
        return response.json()[0]["id"]
    return None


def atualizar_produto(sku, novo_preco, novo_estoque):
    """Atualiza preço e estoque do produto via SKU."""
    produto_id = obter_id_por_sku(sku)
    if produto_id is None:
        print(f"Produto com SKU '{sku}' não encontrado.")
        return

    dados = {
        "regular_price": str(round(novo_preco, 2)),
        "stock_quantity": int(novo_estoque)
    }

    response = wcapi.put(f"products/{produto_id}", dados)
    if response.status_code == 200:
        print(f"✅ Atualizado: SKU {sku} | Preço R${novo_preco} | Estoque {novo_estoque}")
    else:
        print(f"❌ Erro ao atualizar SKU {sku}: {response.text}")


def fetch_produtos_agis():
    """Busca os dados da API da Agis."""
    try:
        response = requests.get(API_URL, headers=HEADERS, params=PARAMS)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Erro {response.status_code}: {response.text}")
            return None
    except Exception as e:
        print(f"Erro ao conectar à API da Agis: {e}")
        return None


def transform_to_table(data):
    """Transforma os dados da Agis em DataFrame somando os estoques de todos os warehouses."""
    produtos = []

    if data and "items" in data:
        for produto in data["items"]:
            sku = produto.get("sku", "N/A")
            nome = produto.get("name", "N/A")
            stock_list = produto.get("stock", [])

            total_estoque = 0
            precos = []

            for s in stock_list:
                try:
                    qty = int(s.get("qty", 0))
                    preco = float(s.get("price", 0))
                    total_estoque += qty
                    precos.append(preco)
                except:
                    continue

            preco_final = max(precos) if precos else 0

            # Ajuste de preço (se > 400)
            if preco_final > 400:
                preco_final = preco_final / 0.80
            else:
                preco_final = 0

            produtos.append({
                "SKU": sku,
                "NOME": nome,
                "QUANTIDADE": total_estoque,
                "PRECO": preco_final
            })

    df = pd.DataFrame(produtos)
    
    # Garantindo que a coluna SKU está formatada corretamente
    df.columns = df.columns.str.strip()
    df["SKU"] = df["SKU"].astype(str)
    
    return df


# --------------------------- ROTINA PRINCIPAL ---------------------------

if __name__ == "__main__":
    produtos_agis_raw = fetch_produtos_agis()
    tabela_agis = transform_to_table(produtos_agis_raw)
    tabela_wc = listar_produtos_woocommerce()

    # Verifique as colunas antes do merge
    print("Colunas tabela_wc:", tabela_wc.columns)
    print("Colunas tabela_agis:", tabela_agis.columns)

    # Renomear coluna se necessário
    if "sku" in tabela_agis.columns:
        tabela_agis.rename(columns={"sku": "SKU"}, inplace=True)

    tabela_final = pd.merge(tabela_wc, tabela_agis, on="SKU", how="inner")

    for _, row in tabela_final.iterrows():
        atualizar_produto(
            sku=row["SKU"],
            novo_preco=row["PRECO"],
            novo_estoque=row["QUANTIDADE"]
        )
