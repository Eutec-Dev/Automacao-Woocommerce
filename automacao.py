# --------------------------- IMPORTAÇÃO DE BIBLIOTECAS NECESSÁRIAS ---------------------------
import requests  # pip install requests
import pandas as pd  # pip install pandas
from woocommerce import API  # pip install woocommerce

# --------------------------- CONFIGURAÇÕES GERAIS E INICIAIS ---------------------------

pd.set_option("display.max_rows", None)  # Exibe todas as linhas
pd.set_option("display.max_columns", None)  # Exibe todas as colunas

# Configurações da API do WooCommerce
wcapi = API(
    url="https://eutec.com.br",  # URL do seu site WordPress
    consumer_key="ck_1fc85717350736c7769d86de5690e839faf1a2f3",  # Consumer Key
    consumer_secret="cs_9dac5695d1f7d1f3320021a0afec3120cea4309f",  # Consumer Secret
    version="wc/v3",
    timeout=10,
)

# Dados da API da Agis
API_URL = "https://vendas.agis.com.br/rest/all/V1/agis/reseller/product/list"
TOKEN = "1cnl71wepg3cqhu3t2nys2jgkks68yng"  # Token de autenticação
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}",
}
PARAMS = {
    "searchCriteria[currentPage]": 1,
    "searchCriteria[pageSize]": 1000,
}

# --------------------------- FUNÇÕES PARA O WOOCOMMERCE ---------------------------

def listar_produtos_gerenciados():
    """Busca produtos que têm 'Gerenciar Estoque' ativado"""
    lista_produtos = []
    pagina = 1

    while True:
        response = wcapi.get("products", params={"per_page": 100, "page": pagina})

        if response.status_code == 200:
            produtos = response.json()
            if not produtos:
                break  

            for produto in produtos:
                if produto.get("manage_stock", False):  
                    sku = produto.get("sku", "Sem SKU")
                    preco = produto.get("price", "Sem Preço")
                    estoque = produto.get("stock_quantity", "Sem Estoque")
                    lista_produtos.append([sku, preco, estoque])

            pagina += 1  

        else:
            print("Erro ao buscar produtos:", response.json())
            break

    df = pd.DataFrame(lista_produtos, columns=["SKU", "Preco", "Estoque"])
    return df

def obter_id_por_sku(sku):
    """Busca o ID do produto no WooCommerce pelo SKU"""
    response = wcapi.get(f"products", params={"sku": sku})
    if response.status_code == 200 and response.json():
        return response.json()[0]["id"]
    return None  

def atualizar_produto(sku, novo_preco, novo_estoque):
    """Atualiza preço e estoque apenas para produtos filtrados"""
    produto_id = obter_id_por_sku(sku)
    if produto_id:
        dados = {
            "regular_price": str(novo_preco),
            "stock_quantity": novo_estoque,
        }
        response = wcapi.put(f"products/{produto_id}", dados)
        if response.status_code == 200:
            print(f"✅ Produto {sku} atualizado com sucesso!")
        else:
            print(f"⚠ Erro ao atualizar produto {sku}: {response.text}")

# --------------------------- FUNÇÕES PARA A API DA AGIS ---------------------------

def fetch_products(api_url, headers, params):
    """Busca os produtos da Agis via API"""
    try:
        response = requests.get(api_url, headers=headers, params=params)
        if response.status_code == 200:
            return response.json()
        print(f"Erro {response.status_code}: {response.text}")
        return None
    except Exception as e:
        print(f"Erro ao conectar à API: {str(e)}")
        return None

def transform_to_table(data):
    """Transforma os dados da API da Agis em um DataFrame"""
    products = []

    if data and "items" in data:
        for product in data["items"]:
            sku = product.get("sku", "N/A")
            name = product.get("name", "N/A")
            stock = product.get("stock", [])

            warehouse_1 = stock[0].get("warehouse", "N/A")
            warehouse_2 = stock[1].get("warehouse", "N/A")

            qty_1 = stock[0].get("qty", "N/A")
            qty_2 = stock[1].get("qty", "N/A")

            price = stock[0].get("price", "N/A")

            # Selecionar o estoque do armazém 7
            warehouse, qty = (warehouse_1, qty_1) if warehouse_1 == 7 else (warehouse_2, qty_2)

            # Aplicar taxa de 15% no preço
            if price > 0:
                price = round(price / 0.85, 2)

            products.append({
                "SKU": sku,
                "NOME": name,
                "WAREHOUSE": warehouse,
                "QUANTIDADE": qty,
                "PRECO": price,
            })

        return pd.DataFrame(products)

    print("Nenhum dado encontrado.")
    return pd.DataFrame()

# --------------------------- ROTINA PRINCIPAL ---------------------------

if __name__ == "__main__":
    # Lista apenas produtos com 'Gerenciar Estoque' ativo
    df_woo = listar_produtos_gerenciados()

    # Busca os dados da Agis
    products_data = fetch_products(API_URL, HEADERS, PARAMS)
    df_agis = transform_to_table(products_data)

    # Faz merge apenas dos SKU que existem nos dois sistemas
    tabela_final = df_woo.merge(df_agis, on="SKU", how="inner")

    # Atualiza os produtos no WooCommerce conforme os dados da Agis
    for _, row in tabela_final.iterrows():
        atualizar_produto(str(row["SKU"]), float(row["PRECO"]), int(row["QUANTIDADE"]))

    print("✅ Atualização concluída!")
