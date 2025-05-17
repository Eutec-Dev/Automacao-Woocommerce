# --------------------------- IMPORTAÇÃO DE BIBLIOTECAS NECESSÁRIAS ---------------------------
import requests # pip install requests
import pandas as pd # pip install pandas
from woocommerce import API # pip install woocommerce

# --------------------------- CONFIGURAÇÕES GERAIS E INICIAIS ---------------------------

# Configuração para exibir todos os dados no console, útil para debug
pd.set_option('display.max_rows', None)  # Exibe todas as linhas
pd.set_option('display.max_columns', None)  # Exibe todas as colunas

# Configurações da API do WooCommerce
wcapi = API(
    url="https://eutec.com.br",  # URL do seu site WordPress
    consumer_key="ck_1fc85717350736c7769d86de5690e839faf1a2f3",  # Sua Consumer Key
    consumer_secret="cs_9dac5695d1f7d1f3320021a0afec3120cea4309f",  # Sua Consumer Secret
    version="wc/v3",  # Versão da API do WooCommerce
    timeout=10  # Tempo limite da requisição
)

# Dados da API da Agis
API_URL = "https://vendas.agis.com.br/rest/all/V1/agis/reseller/product/list"  # PRODUÇÃO
TOKEN = "1cnl71wepg3cqhu3t2nys2jgkks68yng"  # Token de autenticação da API
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}"
}
PARAMS = {
    "searchCriteria[currentPage]": 1, 
    "searchCriteria[pageSize]": 1000    
}

# --------------------------- FUNÇÕES PARA O WOOCOMMERCE ---------------------------

# Função para listar todos os produtos do WooCommerce
def listar_produtos():
    lista_produtos = []
    pagina = 1  # Começa da página 1

    while True:
        # Faz a requisição paginada (100 produtos por página)
        response = wcapi.get("products", params={"per_page": 100, "page": pagina})
        
        if response.status_code == 200:
            produtos = response.json()
            
            if not produtos:
                break  # Sai do loop se não houver mais produtos

            # Adicionar os produtos à lista
            for produto in produtos:
                if produto.get("manage_stock", False): # Filtra apenas os produtos gerenciados
                    id_produto = produto.get("sku", "Sem SKU")
                    preco = produto.get("price", "Sem Preço")
                    estoque = produto.get("stock_quantity", "Sem Estoque")
                    lista_produtos.append([id_produto, preco, estoque])
           
            pagina += 1  
        
        else: 
            print("Erro ao buscar produtos:", response.json())
            break

    # Criar um DataFrame Pandas com os produtos
    df = pd.DataFrame(lista_produtos, columns=["SKU", "Preco", "Estoque"])
    df.drop(df[df["Preco"] == "0.0"].index, inplace=True)  
    df.drop(df[df["Preco"] == "0.00"].index, inplace=True) 
    df.drop(df[df["Estoque"].isna()].index, inplace=True)  
    
    return df

# Atualizar um produto EXISTENTE
def atualizar_produto(sku, novo_preco, novo_estoque):
    produto_id = obter_id_por_sku(sku)
    dados = {
        "regular_price": str(novo_preco),
        "stock_quantity": novo_estoque,  # Substitui o estoque sem somar!
        "manage_stock": True  # Garante que WooCommerce reconheça a gestão de estoque
    }
    response = wcapi.put(f"products/{produto_id}", dados)
    response.encoding = 'utf-8'
    print("Produto Atualizado:", response.text.encode('utf-8', 'ignore').decode('utf-8'))

# --------------------------- FUNÇÕES PARA A API DA AGIS ---------------------------

# Busca os produtos da Agis via API
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

# Transforma os dados da API da Agis em tabela (DataFrame)
def transform_to_table(data):
    if data and "items" in data:
        products = []
        for product in data["items"]:
            sku = product.get("sku", "N/A")
            name = product.get("name", "N/A")
            stock = product.get("stock", [])

            # Soma os estoques dos dois armazéns
            qty_1 = stock[0].get("qty", 0)
            qty_2 = stock[1].get("qty", 0)
            total_qty = qty_1 + qty_2

            price = stock[0].get("price", 0)

            # Aplicar taxa de 20% no preço
            if price > 0:
                price = round(price / 0.80, 2)

            products.append({
                "SKU": sku,
                "NOME": name,
                "QUANTIDADE": total_qty,  # Soma estoque dos dois armazéns
                "PRECO": price,
            })

        return pd.DataFrame(products)

    print("Nenhum dado encontrado.")
    return pd.DataFrame()

# --------------------------- ROTINA PRINCIPAL (EXECUÇÃO) ---------------------------

if __name__ == "__main__":
    # Busca apenas produtos com 'Gerenciar Estoque' ativado
    df_woo = listar_produtos()

    # Busca os dados da Agis
    products_data = fetch_products(API_URL, HEADERS, PARAMS)
    df_agis = transform_to_table(products_data)

    # Faz merge apenas dos SKU existentes nos dois sistemas
    tabela_final = df_woo.merge(df_agis, on="SKU", how="inner")

    # Atualiza os produtos no WooCommerce conforme os dados da Agis
    for _, row in tabela_final.iterrows():
        atualizar_produto(str(row["SKU"]), float(row["PRECO"]), int(row["QUANTIDADE"]))

    print("✅ Atualização concluída!")
