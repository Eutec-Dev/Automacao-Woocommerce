import requests  # Biblioteca para requisições HTTP
import pandas as pd  # Biblioteca para manipulação de tabelas e dados
from woocommerce import API  # Cliente da API WooCommerce
import urllib.parse  # Para codificação de URLs (tratamento de caracteres especiais)

# Configuração do Pandas para exibir todos os dados no console
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)

# Configuração da API WooCommerce (Eutec - Loja Afiliada)
wcapi = API(
    url="https://eutec.com.br",  # URL do WooCommerce
    consumer_key="ck_5506e564a1f28a33558e9da73b33823db3c15510",  # Chave do consumidor
    consumer_secret="cs_07393e037d36912181839d01905909d568448350",  # Segredo do consumidor
    version="wc/v3",  # Versão da API WooCommerce
    timeout=10  # Tempo limite para as requisições
)

# Configuração da API Agis (Distribuidora)
API_URL = "https://vendas.agis.com.br/rest/all/V1/agis/reseller/product/list"
TOKEN = "1cnl71wepg3cqhu3t2nys2jgkks68yng"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}"  # Token de autenticação para acesso à API
}
PARAMS = {
    "searchCriteria[currentPage]": 1,
    "searchCriteria[pageSize]": 2000  # Aumentar limite para pegar todos os produtos da Agis
}

# Função para codificar o SKU corretamente
def encode_sku(sku):
    """ Codifica o SKU para evitar problemas de caracteres especiais e espaços. """
    return urllib.parse.quote_plus(sku.strip())  # Remove espaços extras e aplica encoding correto

# Função para listar TODOS os produtos do WooCommerce (publicados e fora de estoque)
def listar_produtos_wc():
    """ Busca TODOS os produtos publicados no WooCommerce, incluindo os fora de estoque. """
    lista_produtos = []
    pagina = 1

    while True:
        # Requisição para buscar produtos em páginas de 100 itens
        response = wcapi.get("products", params={"per_page": 100, "page": pagina, "status": "publish"})

        if response.status_code == 200:
            produtos = response.json()
            if not produtos:
                break  # Sai do loop quando não houver mais produtos para buscar

            for produto in produtos:
                sku = produto.get("sku", "").strip()
                preco = produto.get("price", "0")
                estoque = produto.get("stock_quantity", 0)
                produto_id = produto.get("id")
                gerencia_estoque = produto.get("manage_stock", False)

                lista_produtos.append([produto_id, sku, preco, estoque, gerencia_estoque])

            pagina += 1  # Passa para a próxima página
        else:
            print("Erro ao buscar produtos WooCommerce:", response.text)
            break

    df = pd.DataFrame(lista_produtos, columns=["ID", "SKU", "Preco", "Estoque", "Gerencia_Estoque"])
    return df

# Função para buscar produtos na API da Agis (Distribuidora)
def fetch_products_agis():
    """ Obtém os produtos da API da Agis para comparar estoque e preço. """
    try:
        response = requests.get(API_URL, headers=HEADERS, params=PARAMS)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Erro {response.status_code} na API Agis:", response.text)
            return None
    except Exception as e:
        print(f"Erro de conexão à API Agis:", str(e))
        return None

# Função para transformar resposta da API Agis em DataFrame
def transform_to_table_agis(data):
    """ Converte a resposta da API Agis em um DataFrame estruturado. """
    if data and "items" in data:
        products = []

        for product in data["items"]:
            sku = product.get("sku", "").strip()
            name = product.get("name", "N/A")
            stock = product.get("stock", [])
            
            qty = 0
            price = 0

            # Busca os dados de estoque e preço no warehouse "007"
            for s in stock:
                if s.get("warehouse") == "007":
                    qty = s.get("qty", 0)
                    price = s.get("price", 0)

            # Ajuste no preço conforme regra (exemplo: margem de 20%)
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
        print("Nenhum dado válido recebido da API Agis.")
        return pd.DataFrame()

# Função para comparar SKUs e preparar a atualização
def comparar_e_atualizar_produtos():
    """ Compara os produtos do WooCommerce com a Agis e atualiza apenas os que têm correspondência. """
    # Buscar dados das duas APIs
    data_wc = listar_produtos_wc()
    data_agis = fetch_products_agis()
    tabela_agis = transform_to_table_agis(data_agis)

    # Filtrar apenas os produtos que existem nas duas bases
    tabela_final = data_wc.merge(tabela_agis, on="SKU", how="inner")  # Faz a junção com correspondência de SKUs

    # Atualizar apenas os produtos que têm estoque e preço na Agis
    for i, row in tabela_final.iterrows():
        sku = row["SKU"]
        preco = row["PRECO"]
        estoque = row["QUANTIDADE"]

        print(f"🔄 Atualizando SKU: '{sku}' com preço {preco} e estoque {estoque}")
        atualizar_produto_wc(sku, preco, estoque)

# Função para atualizar preço e estoque no WooCommerce
def atualizar_produto_wc(sku, novo_preco, novo_estoque):
    """ Atualiza preço e estoque do produto no WooCommerce. """
    sku_encoded = encode_sku(sku)  # Garante que o SKU esteja codificado corretamente
    produto_id = obter_id_por_sku(sku_encoded)
    
    if produto_id is None:
        print(f"❌ SKU '{sku}' não encontrado no WooCommerce.")
        return

    dados = {
        "regular_price": str(novo_preco),
        "stock_quantity": novo_estoque,
        "manage_stock": True,
        "in_stock": novo_estoque > 0,  # Define disponibilidade com base no estoque da Agis
    }

    response = wcapi.put(f"products/{produto_id}", dados)
    if response.status_code == 200:
        print(f"✅ Produto SKU '{sku}' atualizado com sucesso.")
    else:
        print(f"❌ Erro ao atualizar produto SKU '{sku}':", response.text)

# ------------------ Execução principal -------------------
if __name__ == "__main__":
    comparar_e_atualizar_produtos()
