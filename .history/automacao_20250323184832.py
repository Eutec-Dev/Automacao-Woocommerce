import requests # pip install requests
import pandas as pd # pip install pandas
from woocommerce import API # pip install woocommerce

# ----------------------------------------------------------------------------------------- GLOBAL

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

API_URL = "https://vendas.agis.com.br/rest/all/V1/agis/reseller/product/list" # PRODUÇÃO
TOKEN = "1cnl71wepg3cqhu3t2nys2jgkks68yng" 
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}"
}
PARAMS = {
    "searchCriteria[currentPage]": 1, 
    "searchCriteria[pageSize]": 1000    
}

# ----------------------------------------------------------------------------------------- GLOBAL

# ----------------------------------------------------------------------------------------- WOOCOMMERCE

def listar_produtos():
    lista_produtos = []
    pagina = 1  # Começa da página 1

    while True:
        # Faz a requisição paginada (100 produtos por página)
        response = wcapi.get("products", params={"per_page": 100, "page": pagina})
        
        if response.status_code == 200:
            produtos = response.json()
            
            if not produtos:
                break

            # Adicionar os produtos à lista
            for produto in produtos:
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

# Criar um NOVO produto
def criar_produto(nome, preco, estoque):
    dados = {
        "name": nome,
        "regular_price": str(preco),
        "stock_quantity": estoque,
        "type": "simple",  # Produto simples
        "status": "publish"  # Publicar automaticamente
    }
    response = wcapi.post("products", dados)
    print("Produto Criado:", response.json())

# ----------------------------------------------------------------------
def obter_id_por_sku(sku):
    """Busca o ID do produto no WooCommerce com base no SKU"""
    response = wcapi.get(f"products", params={"sku": sku})
    
    if response.status_code == 200 and response.json():
        produto = response.json()[0]  
        return produto["id"]  
    else:
        print(f"Produto com SKU '{sku}' não encontrado!")
        return None  
    
    # ----------------------------------------------------------------------

# Atualizar um produto EXISTENTE
def atualizar_produto(sku, novo_preco, novo_estoque):
    
    produto_id = obter_id_por_sku(sku)
    dados = {
        "regular_price": str(novo_preco),
        "stock_quantity": novo_estoque
    }
    response = wcapi.put(f"products/{produto_id}", dados)
    response.encoding = 'utf-8'
    print("Produto Atualizado:", response.text.encode('utf-8', 'ignore').decode('utf-8'))

# Deletar um produto
def deletar_produto(produto_id):
    response = wcapi.delete(f"products/{produto_id}", params={"force": True})
    print("Produto Deletado:", response.json())

# Buscar um único produto pelo ID
def buscar_produto(produto_id):
    response = wcapi.get(f"products/{produto_id}")
    if response.status_code == 200:
        produto = response.json()
        print(f"Produto encontrado: {produto['name']} - Preço: {produto['price']}")
    else:
        print("Produto não encontrado.")
#---------------------------------------------------------------------------------- WOOCOMMERCE

#---------------------------------------------------------------------------------- AGIS

def fetch_products(api_url, headers, params):
    
    try:
        response = requests.get(api_url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            return data
        else:
            print(f"Erro {response.status_code}: {response.text}")
            return None
    except Exception as e:
        print(f"Erro ao conectar à API: {str(e)}")
        return None


def transform_to_table(data):
  
    warehouse = 0
    qty = 0
    
    if data and "items" in data:
        
        products = []

        for product in data["items"]:
            
            sku = product.get("sku", "N/A")
            name = product.get("name", "N/A")
            stock = product.get("stock", [])
            warehouse_1 = int(stock[0].get("warehouse", "N/A"))
            warehouse_2 = int(stock[1].get("warehouse", "N/A"))
            #warehouse_3 = int(stock[2].get("warehouse", "N/A"))
            qty_1 = stock[0].get("qty", "N/A")
            qty_2 = stock[1].get("qty", "N/A")
            #qty_3 = stock[2].get("qty", "N/A")
            price = stock[0].get("price", "N/A")
            
            if(sku == "210-BLVM-VFLK"):
                print("W1:" + warehouse_1)
                print("QTD_1:" + qty_1)
                print("W2:" + warehouse_1)
                print("QTD_2:" + qty_1)
            
            if(warehouse_1 == 7):    
                warehouse = warehouse_1
                qty = qty_1
                
            if(warehouse_2 == 7):    
                warehouse = warehouse_2
                qty = qty_2
                
            # if(warehouse_3 == 7):    
            #     warehouse = warehouse_3
            #     qty = qty_3
            
            # if(price > 487):
            #     #price = price/0.96  # 4% ORIGINAL
            #     price = price/0.875  # 12,5%  
            # else:
            #     price = 0   
            
            price = price/0.875 # TESTE
            
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
#---------------------------------------------------------------------------------- AGIS

if __name__ == "__main__":
    
    i = 0
    products_data = fetch_products(API_URL, HEADERS, PARAMS)
    tabela2 = transform_to_table(products_data)
    tabela2.to_excel("C:\\TRABALHOS_AUTONOMOS\\ACYR_MONTEIRO\\TESTE.xlsx",index=False)
    
    # df = listar_produtos()
    # tabela_final = df.merge(tabela2, on="SKU", how="inner")  
    
    # Editar os produtos no WordPress
    # while i < len(tabela_final):
    #     atualizar_produto(str(tabela_final.iloc[i,0]), float(tabela_final.iloc[i,6]), int(tabela_final.iloc[i,5]))
    #     i = i+1
    