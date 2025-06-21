# --------------------------- IMPORTAÇÃO DE BIBLIOTECAS NECESSÁRIAS ---------------------------
import requests
import pandas as pd
from woocommerce import API
from tqdm import tqdm  # pip install tqdm

# --------------------------- CONFIGURAÇÕES GERAIS E INICIAIS ---------------------------
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)

# WooCommerce
wcapi = API(
    url="https://eutec.com.br",
    consumer_key="ck_1fc85717350736c7769d86de5690e839faf1a2f3",
    consumer_secret="cs_9dac5695d1f7d1f3320021a0afec3120cea4309f",
    version="wc/v3",
    timeout=15
)

# API Agis
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

# --------------------------- FUNÇÕES DO WOOCOMMERCE ---------------------------

def listar_produtos():
    produtos = []
    pagina = 1

    while True:
        response = wcapi.get("products", params={"per_page": 100, "page": pagina})
        if response.status_code != 200:
            print("Erro ao buscar produtos:", response.json())
            break

        dados = response.json()
        if not dados:
            break

        for prod in dados:
            produtos.append({
                "SKU": prod.get("sku", ""),
                "Preco": float(prod.get("price") or 0),
                "Estoque": prod.get("stock_quantity", 0)
            })

        pagina += 1

    df = pd.DataFrame(produtos)
    df = df[(df["Preco"] > 0) & (df["Estoque"].notna())]
    return df

def obter_id_por_sku(sku):
    response = wcapi.get("products", params={"sku": sku})
    if response.status_code == 200 and response.json():
        return response.json()[0]["id"]
    return None

def atualizar_produto(sku, preco, estoque):
    produto_id = obter_id_por_sku(sku)
    if produto_id is None:
        print(f"Produto não encontrado para SKU: {sku}")
        return

    payload = {
        "regular_price": str(round(preco, 2)),
        "stock_quantity": int(estoque)
    }

    response = wcapi.put(f"products/{produto_id}", payload)
    if response.status_code == 200:
        print(f"Atualizado: SKU {sku}")
    else:
        print(f"Erro ao atualizar SKU {sku}: {response.text}")

# --------------------------- FUNÇÕES DA API AGIS ---------------------------

def fetch_products():
    try:
        response = requests.get(API_URL, headers=HEADERS, params=PARAMS)
        if response.status_code == 200:
            return response.json()
        print(f"Erro {response.status_code}: {response.text}")
    except Exception as e:
        print(f"Erro de conexão: {str(e)}")
    return None

def transform_to_table(data):
    if not data or "items" not in data:
        print("Nenhum dado encontrado.")
        return pd.DataFrame()

    produtos = []
    for item in data["items"]:
        sku = item.get("sku", "")
        nome = item.get("name", "")
        stock = item.get("stock", [])

        preco, estoque = 0, 0
        for s in stock:
            if s.get("warehouse") == 7:
                preco = float(s.get("price", 0))
                estoque = int(float(s.get("qty", 0)))
                break

        preco_corrigido = preco / 0.80 

        produtos.append({
            "SKU": sku,
            "NOME": nome,
            "PRECO_AGIS": preco_corrigido,
            "ESTOQUE_AGIS": estoque
        })

    return pd.DataFrame(produtos)

# --------------------------- ROTINA PRINCIPAL ---------------------------

if __name__ == "__main__":
    print("🔄 Buscando produtos da Agis...")
    dados_agis = fetch_products()
    tabela_agis = transform_to_table(dados_agis)

    print("📦 Listando produtos do WooCommerce...")
    produtos_wc = listar_produtos()

    print("🔗 Combinando dados...")
    tabela_final = produtos_wc.merge(tabela_agis, on="SKU", how="inner")

    print("🚀 Atualizando produtos...")
    for _, linha in tqdm(tabela_final.iterrows(), total=tabela_final.shape[0]):
        atualizar_produto(linha["SKU"], linha["PRECO_AGIS"], linha["ESTOQUE_AGIS"])
