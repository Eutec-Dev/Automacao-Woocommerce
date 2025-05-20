import requests  # Para requisições HTTP
import pandas as pd  # Para manipulação de dados em tabelas
from woocommerce import API  # Cliente WooCommerce API
import urllib.parse  # Para URL encode do SKU

# Configuração para exibir todos os dados no console (debug)
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)

# Configurações da API WooCommerce
wcapi = API(
    url="https://eutec.com.br",  # URL do WooCommerce
    consumer_key="ck_5506e564a1f28a33558e9da73b33823db3c15510",
    consumer_secret="cs_07393e037d36912181839d01905909d568448350",
    version="wc/v3",
    timeout=10
)

# Configurações da API da Agis
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

# Função para listar todos os produtos do WooCommerce (com gerenciador de estoque ativo)
def listar_produtos():
    lista_produtos = []
    pagina = 1

    while True:
        # Busca produtos paginados com 100 por página
        response = wcapi.get("products", params={"per_page": 100, "page": pagina})

        if response.status_code == 200:
            produtos = response.json()

            if not produtos:
                break  # Sai quando não tiver mais produtos

            for produto in produtos:
                # Verifica se gerenciador de estoque está ativo para atualizar somente estes
                if produto.get("manage_stock", False):
                    sku = produto.get("sku", "").strip()
                    preco = produto.get("price", "0")
                    estoque = produto.get("stock_quantity", 0)
                    produto_id = produto.get("id")
                    lista_produtos.append([produto_id, sku, preco, estoque])

            pagina += 1
        else:
            print("Erro ao buscar produtos:", response.text)
            break

    # Cria DataFrame com os produtos filtrados
    df = pd.DataFrame(lista_produtos, columns=["ID", "SKU", "Preco", "Estoque"])

    # Remove produtos sem preço ou estoque válido
    df = df[df["Preco"].astype(float) > 0]
    df = df[df["Estoque"].notna()]

    return df

# Função para buscar ID do produto WooCommerce por SKU (com URL encode e strip)
def obter_id_por_sku(sku):
    sku = sku.strip()
    sku_encoded = urllib.parse.quote(sku, safe='')  # URL encode do SKU

    print(f"Buscando produto com SKU codificado: '{sku_encoded}'")
    response = wcapi.get("products", params={"sku": sku_encoded})

    if response.status_code == 200 and response.json():
        print("Resposta da API para busca de SKU:", response.json())
        produto = response.json()[0]
        return produto["id"]
    else:
        print(f"Produto com SKU '{sku}' não encontrado ou erro na API.")
        return None

# Função para atualizar produto existente no WooCommerce (preço, estoque, gerenciador ativo)
def atualizar_produto(sku, novo_preco, novo_estoque):
    produto_id = obter_id_por_sku(sku)
    if produto_id is None:
        print(f"Não foi possível atualizar o produto com SKU '{sku}': ID não encontrado.")
        return

    dados = {
        "regular_price": str(novo_preco),
        "stock_quantity": novo_estoque,
        "manage_stock": True,  # Garante que o gerenciador de estoque está ativo
        "in_stock": novo_estoque > 0,  # Atualiza status de estoque
    }

    response = wcapi.put(f"products/{produto_id}", dados)
    if response.status_code == 200:
        print(f"Produto SKU '{sku}' atualizado com sucesso.")
    else:
        print(f"Erro ao atualizar produto SKU '{sku}':", response.text)

# Função para buscar produtos na API da Agis
def fetch_products(api_url, headers, params):
    try:
        response = requests.get(api_url, headers=headers, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Erro {response.status_code} ao buscar produtos da Agis:", response.text)
            return None
    except Exception as e:
        print(f"Erro de conexão à API da Agis:", str(e))
        return None

# Transforma dados da API da Agis em DataFrame
def transform_to_table(data):
    if data and "items" in data:
        products = []

        for product in data["items"]:
            sku = product.get("sku", "").strip()
            name = product.get("name", "N/A")
            stock = product.get("stock", [])
            
            # Inicializa variáveis para estoque e preço
            qty = 0
            price = 0

            # Busca estoque e preço no warehouse "007" (ou outro critério)
            for s in stock:
                if s.get("warehouse") == "007":
                    qty = s.get("qty", 0)
                    price = s.get("price", 0)

            # Ajusta preço conforme regra (exemplo)
            if price > 0:
                price = price / 0.80
            else:
                price = 0

            products.append({
                "SKU": sku,
                "NOME": name,
                "QUANTIDADE": int(qty),
                "PRECO": float(price)
            })

        return pd.DataFrame(products)
    else:
        print("Nenhum dado válido recebido da API da Agis.")
        return pd.DataFrame()

# ------------------ Execução principal -------------------

if __name__ == "__main__":
    # Busca produtos da API Agis
    data_agis = fetch_products(API_URL, HEADERS, PARAMS)
    tabela_agis = transform_to_table(data_agis)

    # Lista produtos do WooCommerce com estoque gerenciado ativo
    tabela_wc = listar_produtos()

    # Faz merge dos produtos via SKU (produtos que existem nas duas bases)
    tabela_final = tabela_wc.merge(tabela_agis, on="SKU", how="inner")

    # Atualiza os produtos conforme dados da Agis
    for i, row in tabela_final.iterrows():
        sku = row["SKU"]
        preco = row["PRECO"]
        estoque = row["QUANTIDADE"]
        print(f"Atualizando SKU: '{sku}' com preço {preco} e estoque {estoque}")
        atualizar_produto(sku, preco, estoque)
