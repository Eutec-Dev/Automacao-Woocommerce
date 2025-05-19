# ---------------------- IMPORTAÇÃO DE BIBLIOTECAS NECESSÁRIAS ----------------------
import requests  # Requisições HTTP
import pandas as pd  # Manipulação de tabelas
from woocommerce import API  # Conexão com WooCommerce
import urllib.parse  # Codificação de URLs (necessário para SKUs especiais)

# ---------------------- CONFIGURAÇÕES INICIAIS ----------------------
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)

# Configuração da API WooCommerce
wcapi = API(
    url="https://eutec.com.br",
    consumer_key="ck_5506e564a1f28a33558e9da73b33823db3c15510",
    consumer_secret="cs_07393e037d36912181839d01905909d568448350",
    version="wc/v3",
    timeout=15
)

# Configuração da API Agis
API_URL = "https://vendas.agis.com.br/rest/all/V1/agis/reseller/product/list"
TOKEN = "1cnl71wepg3cqhu3t2nys2jgkks68yng"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}"
}
PARAMS = {
    "searchCriteria[currentPage]": 1,
    "searchCriteria[pageSize]": 1000
}

# ---------------------- FUNÇÕES WOO COMMERCE ----------------------

# Lista todos os produtos WooCommerce com seus SKUs, preços e estoque
def listar_produtos():
    lista_produtos = []
    pagina = 1

    while True:
        response = wcapi.get("products", params={"per_page": 100, "page": pagina})
        if response.status_code != 200:
            print("Erro ao buscar produtos:", response.text)
            break

        produtos = response.json()
        if not produtos:
            break  # Fim da paginação

        for produto in produtos:
            # Apenas adiciona se manage_stock for True
            if produto.get("manage_stock", False):
                lista_produtos.append([
                    produto.get("sku", "Sem SKU"),
                    produto.get("price", 0),
                    produto.get("stock_quantity", 0)
                ])
        pagina += 1

    df = pd.DataFrame(lista_produtos, columns=["SKU", "Preco", "Estoque"])
    df = df[df["Preco"].astype(float) > 0]
    df = df.dropna(subset=["Estoque"])
    return df

# Obtém o ID do produto pelo SKU, sem filtro no manage_stock (para buscar todos)
def obter_id_por_sku(sku):
    sku = sku.strip()
    sku_encoded = urllib.parse.quote(sku, safe='')  # Codifica o SKU para caracteres especiais
    try:
        response = wcapi.get("products", params={"sku": sku_encoded})
        if response.status_code == 200 and response.json():
            produto = response.json()[0]
            return produto["id"]
        else:
            print(f"[⚠️] Produto com SKU '{sku}' não encontrado.")
    except Exception as e:
        print(f"[ERRO] Falha ao obter produto SKU '{sku}': {e}")
    return None

# Atualiza um produto existente com novo preço, estoque e força gerenciar estoque
def atualizar_produto(sku, novo_preco, novo_estoque):
    produto_id = obter_id_por_sku(sku)
    if produto_id:
        dados = {
            "regular_price": str(novo_preco),
            "stock_quantity": int(novo_estoque),
            "manage_stock": True,  # Força ativar gerenciador de estoque
            "stock_status": "instock" if int(novo_estoque) > 0 else "outofstock"
        }
        try:
            response = wcapi.put(f"products/{produto_id}", dados)
            print(f"[OK] SKU '{sku}' atualizado com preço R${novo_preco:.2f} e estoque {novo_estoque}")
        except Exception as e:
            print(f"[ERRO] Falha ao atualizar SKU '{sku}': {e}")

# ---------------------- FUNÇÕES API AGIS ----------------------

# Busca os produtos da API Agis
def fetch_products(api_url, headers, params):
    try:
        response = requests.get(api_url, headers=headers, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"[ERRO AGIS] {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[ERRO] Conexão com API Agis: {e}")
    return None

# Transforma resposta da API Agis em DataFrame
def transform_to_table(data):
    produtos = []

    if not data or "items" not in data:
        print("Nenhum dado retornado pela API da Agis.")
        return pd.DataFrame()

    for produto in data["items"]:
        sku = produto.get("sku", "N/A")
        nome = produto.get("name", "N/A")
        stock = produto.get("stock", [])

        if len(stock) < 2:
            continue  # Ignora se não há dados de estoque suficientes

        warehouse = 0
        qty = 0

        # Verifica o warehouse 7 nos dois registros
        for s in stock:
            if s.get("warehouse") == 7 or s.get("warehouse") == "007":  # Atento para string '007'
                warehouse = s.get("warehouse")
                qty = s.get("qty")
                preco = s.get("price", 0)
                break
        else:
            continue  # Se não encontrou warehouse 7, pula

        preco_reajustado = preco / 0.80 if preco > 0 else 0  # Reajuste de preço

        produtos.append({
            "SKU": sku,
            "NOME": nome,
            "WAREHOUSE": warehouse,
            "QUANTIDADE": qty,
            "PRECO": preco_reajustado
        })

    return pd.DataFrame(produtos)

# ---------------------- EXECUÇÃO PRINCIPAL ----------------------

if __name__ == "__main__":
    produtos_agis = fetch_products(API_URL, HEADERS, PARAMS)
    tabela_agis = transform_to_table(produtos_agis)

    tabela_woo = listar_produtos()

    # Merge entre produtos da loja e da Agis (apenas os que estão em ambas)
    tabela_final = tabela_woo.merge(tabela_agis, on="SKU", how="inner")

    # Atualiza os produtos da loja com base nos dados da Agis
    for _, row in tabela_final.iterrows():
        sku = str(row["SKU"])
        novo_preco = float(row["PRECO"])
        novo_estoque = int(row["QUANTIDADE"])
        atualizar_produto(sku, novo_preco, novo_estoque)
