# --------------------------- IMPORTAÇÃO DE BIBLIOTECAS NECESSÁRIAS --------------------------- 
import requests  # Requisições HTTP
import pandas as pd  # Manipulação de dados em tabelas
from woocommerce import API  # Biblioteca para consumir a API do WooCommerce
import urllib.parse  # Para codificar SKUs com caracteres especiais

# --------------------------- CONFIGURAÇÕES GERAIS E INICIAIS ---------------------------

# Configuração para exibir todos os dados no console, útil para debug
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)

# Configurações da API do WooCommerce
wcapi = API(
    url="https://eutec.com.br",
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

# --------------------------- FUNÇÕES PARA O WOOCOMMERCE ---------------------------

# Função para listar todos os produtos publicados com preços e estoques válidos
def listar_produtos():
    lista_produtos = []
    pagina = 1

    while True:
        response = wcapi.get("products", params={"per_page": 100, "page": pagina})
        
        if response.status_code == 200:
            produtos = response.json()
            if not produtos:
                break  # Fim da lista

            for produto in produtos:
                id_produto = produto.get("sku", "Sem SKU")
                preco = produto.get("price", "Sem Preço")
                estoque = produto.get("stock_quantity", "Sem Estoque")
                lista_produtos.append([id_produto, preco, estoque])
            
            pagina += 1
        else:
            print("Erro ao buscar produtos:", response.json())
            break

    # Cria DataFrame e remove produtos com preço zero ou estoque ausente
    df = pd.DataFrame(lista_produtos, columns=["SKU", "Preco", "Estoque"])
    df.drop(df[df["Preco"] == "0.0"].index, inplace=True)
    df.drop(df[df["Preco"] == "0.00"].index, inplace=True)
    df.drop(df[df["Estoque"].isna()].index, inplace=True)
    
    return df

# Criação de um novo produto (não utilizado no fluxo atual)
def criar_produto(nome, preco, estoque):
    dados = {
        "name": nome,
        "regular_price": str(preco),
        "stock_quantity": estoque,
        "type": "simple",
        "status": "publish"
    }
    response = wcapi.post("products", dados)
    print("Produto Criado:", response.json())

# Função atualizada para obter ID do produto apenas se gerenciador de estoque estiver ativo
def obter_id_por_sku(sku):
    sku = sku.strip()
    sku_encoded = urllib.parse.quote(sku, safe='')  # Codifica o SKU

    response = wcapi.get("products", params={"sku": sku_encoded})

    if response.status_code == 200 and response.json():
        produto = response.json()[0]

        # Verifica se o gerenciador de estoque está ativo
        if produto.get("manage_stock", False):
            return produto["id"]
        else:
            print(f"[IGNORADO] SKU '{sku}' não usa controle de estoque (manage_stock=False).")
            return None
    else:
        print(f"[⚠️] Produto com SKU '{sku}' não encontrado. Status: {response.status_code}")
        return None

# Atualiza produto com novo preço e estoque (caso gerencie estoque)
def atualizar_produto(sku, novo_preco, novo_estoque):
    produto_id = obter_id_por_sku(sku)
    if produto_id:
        dados = {
            "regular_price": str(novo_preco),
            "stock_quantity": novo_estoque
        }
        response = wcapi.put(f"products/{produto_id}", dados)
        print(f"[✓] SKU '{sku}' atualizado com sucesso.")
    else:
        print(f"[X] SKU '{sku}' ignorado na atualização.")

# Deleta produto pelo ID (não usado atualmente)
def deletar_produto(produto_id):
    response = wcapi.delete(f"products/{produto_id}", params={"force": True})
    print("Produto Deletado:", response.json())

# Busca produto pelo ID (debug)
def buscar_produto(produto_id):
    response = wcapi.get(f"products/{produto_id}")
    if response.status_code == 200:
        produto = response.json()
        print(f"Produto encontrado: {produto['name']} - Preço: {produto['price']}")
    else:
        print("Produto não encontrado.")

# --------------------------- FUNÇÕES PARA A API DA AGIS ---------------------------

# Busca os produtos na API da Agis
def fetch_products(api_url, headers, params):
    try:
        response = requests.get(api_url, headers=headers, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Erro {response.status_code}: {response.text}")
            return None
    except Exception as e:
        print(f"Erro ao conectar à API: {str(e)}")
        return None

# Transforma os dados da API da Agis em DataFrame
def transform_to_table(data):
    warehouse = 0
    qty = 0

    if data and "items" in data:
        products = []

        for product in data["items"]:
            sku = product.get("sku", "N/A")
            name = product.get("name", "N/A")
            stock = product.get("stock", [])

            warehouse_1 = int(stock[0].get("warehouse", 0))
            warehouse_2 = int(stock[1].get("warehouse", 0))
            qty_1 = stock[0].get("qty", 0)
            qty_2 = stock[1].get("qty", 0)
            price = stock[0].get("price", 0)

            # Se houver estoque no warehouse 7, usamos esse
            if warehouse_1 == 7:
                warehouse = warehouse_1
                qty = qty_1
            if warehouse_2 == 7:
                warehouse = warehouse_2
                qty = qty_2

            # Reajuste de preço (se > 0, aplica margem de 25%)
            if price > 0:
                price = price / 0.80
            else:
                price = 0

            products.append({
                "SKU": sku,
                "NOME": name,
                "WAREHOUSE": warehouse,
                "QUANTIDADE": qty,
                "PRECO": price
            })

        df = pd.DataFrame(products)
        return df
    else:
        print("Nenhum dado encontrado.")
        return pd.DataFrame()

# --------------------------- ROTINA PRINCIPAL ---------------------------

if __name__ == "__main__":
    # Busca e transforma dados da Agis
    products_data = fetch_products(API_URL, HEADERS, PARAMS)
    tabela2 = transform_to_table(products_data)

    # Lista produtos atuais do WooCommerce
    df = listar_produtos()

    # Faz o merge entre dados da loja e dados da Agis
    tabela_final = df.merge(tabela2, on="SKU", how="inner")

    # Atualiza os produtos com os novos preços e estoques
    for i in range(len(tabela_final)):
        sku = str(tabela_final.iloc[i, 0])
        estoque = int(tabela_final.iloc[i, 5])
        preco = float(tabela_final.iloc[i, 6])
        atualizar_produto(sku, preco, estoque)
