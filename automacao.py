import requests
import pandas as pd
from woocommerce import API
import time  # Para evitar problemas de taxa de requisição

# Configuração da API WooCommerce
wcapi = API(
    url="https://eutec.com.br",
    consumer_key="ck_5506e564a1f28a33558e9da73b33823db3c15510",
    consumer_secret="cs_07393e037d36912181839d01905909d568448350",
    version="wc/v3",
    timeout=20
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

def listar_produtos():
    """Busca todos os produtos do WooCommerce com estoque gerenciado ativo"""
    lista = []
    page = 1

    while True:
        response = wcapi.get("products", params={"per_page": 100, "page": page})

        if response.status_code != 200:
            print("Erro ao buscar produtos:", response.text)
            break

        produtos = response.json()
        if not produtos:
            break

        for produto in produtos:
            if produto.get("manage_stock", False):
                sku = produto.get("sku", "").strip()  # Mantendo espaços e caracteres
                preco = produto.get("price", "0")
                estoque = produto.get("stock_quantity", 0)
                produto_id = produto.get("id")
                lista.append([produto_id, sku, preco, estoque])

        page += 1

    df = pd.DataFrame(lista, columns=["ID", "SKU", "Preco", "Estoque"])
    df = df[df["Preco"].astype(float) > 0]
    df = df[df["Estoque"].notna()]

    return df

def obter_id_por_sku(sku):
    """Busca o ID do produto na WooCommerce via SKU"""
    sku = sku.strip()  # Mantendo espaços
    response = wcapi.get("products", params={"sku": sku})  # Enviando SKU sem codificação

    if response.status_code == 200 and response.json():
        return response.json()[0]["id"]
    return None

def atualizar_produto(sku, novo_preco, novo_estoque, tentativas=3):
    """Atualiza preço e estoque do produto na WooCommerce, tentando várias vezes em caso de falha"""
    produto_id = obter_id_por_sku(sku)
    if not produto_id:
        print(f"[!] SKU '{sku}' não encontrado na loja.")
        return

    dados = {
        "regular_price": str(novo_preco),
        "stock_quantity": novo_estoque,
        "manage_stock": True,
        "in_stock": novo_estoque > 0
    }

    for tentativa in range(tentativas):
        response = wcapi.put(f"products/{produto_id}", dados)
        if response.status_code == 200:
            print(f"[✓] SKU '{sku}' atualizado com sucesso.")
            return
        else:
            print(f"[X] Tentativa {tentativa+1} falhou ao atualizar SKU '{sku}': {response.text}")
            time.sleep(2)  # Espera um pouco antes de tentar novamente

    print(f"[!] Falha ao atualizar SKU '{sku}' após {tentativas} tentativas. Pulando para o próximo.")

def fetch_products(api_url, headers, params):
    """Busca produtos da API Agis"""
    try:
        response = requests.get(api_url, headers=headers, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            print("Erro ao buscar da Agis:", response.status_code, response.text)
            return None
    except Exception as e:
        print("Erro de conexão à API Agis:", str(e))
        return None

def transform_to_table(data):
    """Converte os dados da Agis em uma tabela com SKU, nome, estoque e preço"""
    if not data or "items" not in data:
        print("Dados inválidos da Agis.")
        return pd.DataFrame()

    produtos = []

    for item in data["items"]:
        sku = item.get("sku", "").strip()  # Mantendo espaços e caracteres especiais
        name = item.get("name", "")
        stock = item.get("stock", [])

        qty = 0
        price = 0

        for s in stock:
            if s.get("warehouse") == "007":
                qty = s.get("qty", 0)
                price = s.get("price", 0)

        if price > 0:
            price = price / 0.80  # margem de 25%
        else:
            price = 0

        produtos.append({
            "SKU": sku,
            "NOME": name,
            "QUANTIDADE": int(qty),
            "PRECO": float(price)
        })

    return pd.DataFrame(produtos)

# Execução principal
if __name__ == "__main__":
    print("🔄 Buscando produtos da Agis...")
    dados_agis = fetch_products(API_URL, HEADERS, PARAMS)
    tabela_agis = transform_to_table(dados_agis)

    print("🛒 Buscando produtos da WooCommerce...")
    tabela_wc = listar_produtos()

    # Mantendo espaços nos SKUs antes do merge
    tabela_wc["SKU"] = tabela_wc["SKU"].str.strip()
    tabela_agis["SKU"] = tabela_agis["SKU"].str.strip()

    print("\n🔍 SKUs WooCommerce:", tabela_wc["SKU"].tolist())
    print("🔍 SKUs Agis:", tabela_agis["SKU"].tolist())

    tabela_final = tabela_wc.merge(tabela_agis, on="SKU", how="inner")

    print(f"🔧 {len(tabela_final)} produtos em comum encontrados. Iniciando atualização...\n")

    for _, row in tabela_final.iterrows():
        sku = row["SKU"]
        preco = row["PRECO"]
        estoque = row["QUANTIDADE"]
        print(f"Atualizando SKU: '{sku}' | Preço: R${preco:.2f} | Estoque: {estoque}")
        atualizar_produto(sku, preco, estoque)
        time.sleep(1)  # Mantém tempo entre requisições

    print("\n✅ Atualização concluída.")
