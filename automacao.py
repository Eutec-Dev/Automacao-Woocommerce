import requests
import pandas as pd
from woocommerce import API
import time
import urllib.parse

# --- Configuração da API WooCommerce ---
wcapi = API(
    url="https://eutec.com.br",
    consumer_key="ck_5506e564a1f28a33558e9da73b33823db3c15510",
    consumer_secret="cs_07393e037d36912181839d01905909d568448350",
    version="wc/v3",
    timeout=20
)

# --- Configuração da API Agis ---
API_URL = "https://vendas.agis.com.br/rest/all/V1/agis/reseller/product/list"
TOKEN = "1cnl71wepg3cqhu3t2nys2jgkks68yng"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}"
}

# --- Funções de Busca em Massa ---

def get_all_woocommerce_manageable_products():
    """
    Busca TODOS os produtos da WooCommerce que têm 'manage_stock' ativo.
    Retorna um DataFrame do Pandas.
    """
    all_wc_products_data = []
    page = 1
    per_page = 100 # Número de produtos por página

    print("🔧 Buscando produtos da WooCommerce com gerenciamento de estoque ativo...")
    while True:
        try:
            response = wcapi.get("products", params={"per_page": per_page, "page": page, "stock_status": "instock"})
            
            if response.status_code != 200:
                print(f"❌ Erro ao buscar produtos da WooCommerce (Página {page}): {response.status_code} - {response.text}")
                break

            products = response.json()
            if not products:
                break # Sem mais produtos

            # Filtra APENAS produtos que têm gerenciamento de estoque ativo e um SKU
            manageable_products = [
                {"id": p["id"], "sku": p.get("sku", "").strip(), "regular_price": float(p.get("regular_price", 0)), "stock_quantity": p.get("stock_quantity", 0)}
                for p in products if p.get("manage_stock", False) and p.get("sku")
            ]
            all_wc_products_data.extend(manageable_products)
            
            print(f"   ✅ Página {page} da WooCommerce processada. Produtos encontrados com estoque gerenciado: {len(manageable_products)}")
            
            page += 1
            time.sleep(0.1) # Pequeno atraso para respeitar os limites de taxa da WC

        except requests.exceptions.Timeout:
            print(f"⏰ Timeout ao buscar produtos da WooCommerce na página {page}. Tentando novamente...")
            time.sleep(5) # Espera e tenta novamente
            continue
        except requests.exceptions.RequestException as e:
            print(f"❌ Erro de requisição ao buscar produtos da WooCommerce na página {page}: {e}")
            break

    if not all_wc_products_data:
        print("⚠️ Nenhum produto com gerenciamento de estoque ativo encontrado na WooCommerce.")
        return pd.DataFrame() # Retorna um DataFrame vazio

    print(f"☑️ Total de produtos da WooCommerce com estoque gerenciado: {len(all_wc_products_data)}")
    return pd.DataFrame(all_wc_products_data)

def get_all_agis_products():
    """
    Busca TODOS os produtos da API da Agis (paginado).
    Retorna um DataFrame do Pandas.
    """
    all_agis_products_data = []
    page = 1
    page_size = 1000 # O máximo que a Agis API pode retornar por página

    print("🔧 Buscando todos os produtos da Agis...")
    while True:
        params = {
            "searchCriteria[currentPage]": page,
            "searchCriteria[pageSize]": page_size
        }
        try:
            response = requests.get(API_URL, headers=HEADERS, params=params, timeout=30) # Aumentar timeout para Agis
            
            if response.status_code != 200:
                print(f"❌ Erro ao buscar produtos da Agis (Página {page}): {response.status_code} - {response.text}")
                break

            data = response.json()
            items = data.get("items", [])
            if not items:
                break # Sem mais itens

            for item in items:
                sku = item.get("sku", "").strip()
                if not sku:
                    continue

                qty = 0
                price = 0
                for s in item.get("stock", []):
                    if s.get("warehouse") in ["007", "004"]:  # Inclui ambos os depósitos
                        qty += s.get("qty", 0)  # Soma os estoques
                        if s.get("price", 0) > 0:
                            price = s.get("price", 0)  # Usa o preço do warehouse que tem estoque disponível

                final_price = price / 0.80 if price > 0 else 0 # Aplica margem
                all_agis_products_data.append({"sku": sku, "agis_price": final_price, "agis_stock": qty})
            
            print(f"   ✅ Página {page} da Agis processada. Itens encontrados: {len(items)}")

            total_count = data.get("total_count", 0)
            if total_count > 0 and len(all_agis_products_data) >= total_count:
                break # Já buscamos tudo

            page += 1
            time.sleep(0.5) # Pequeno atraso entre as requisições em lote da Agis

        except requests.exceptions.Timeout:
            print(f"⏰ Timeout ao buscar produtos da Agis na página {page}. Tentando novamente...")
            time.sleep(10) # Espera mais tempo e tenta novamente
            continue
        except requests.exceptions.RequestException as e:
            print(f"❌ Erro de requisição ao buscar produtos da Agis na página {page}: {e}")
            break

    if not all_agis_products_data:
        print("⚠️ Nenhum dado de produto encontrado na Agis.")
        return pd.DataFrame() # Retorna um DataFrame vazio

    print(f"☑️ Total de produtos da Agis processados: {len(all_agis_products_data)}")
    return pd.DataFrame(all_agis_products_data)

# --- Função Principal de Atualização ---

def update_products_in_bulk():
    """
    Coordena a busca de dados, comparação e atualização em lote dos produtos.
    """
    print("🚀 Iniciando o processo de atualização de produtos...")

    # 1. Obter todos os produtos da WooCommerce com gerenciamento de estoque ativo
    df_wc = get_all_woocommerce_manageable_products()
    if df_wc.empty:
        print("Nenhum produto da WooCommerce com estoque gerenciado para processar.")
        return

    # 2. Obter todos os produtos da Agis
    df_agis = get_all_agis_products()
    if df_agis.empty:
        print("Nenhum dado da Agis para comparar.")
        return

    # 3. Criar a tabela de produtos para comparação (JOIN dos DataFrames)
    # Garante que 'sku' esteja limpo em ambos os DataFrames para o merge
    df_wc['sku'] = df_wc['sku'].str.strip()
    df_agis['sku'] = df_agis['sku'].str.strip()
    
    # Merge (LEFT JOIN) para manter todos os produtos da WooCommerce e adicionar dados da Agis
    # Isso é MUITO mais rápido do que buscar SKU por SKU
    df_merged = pd.merge(df_wc, df_agis, on='sku', how='left', suffixes=('_wc', '_agis'))

    print("\n📊 Tabela de produtos para comparação criada. Identificando atualizações necessárias...")

    updates_payload = {"update": []}
    products_to_update_count = 0

    # Itera sobre o DataFrame mesclado para identificar as mudanças
    for index, row in df_merged.iterrows():
        product_id = row['id']
        sku = row['sku']
        wc_price = row['regular_price_wc']
        wc_stock = row['stock_quantity_wc']
        
        agis_price = row['agis_price']
        agis_stock = row['agis_stock']

        needs_update = False
        new_price = wc_price
        new_stock = wc_stock
        new_in_stock_status = True # Assume que estará em estoque se houver qty > 0

        # Verifica se o produto foi encontrado na Agis
        if pd.isna(agis_price) or pd.isna(agis_stock):
            print(f"   [!] SKU '{sku}' (ID: {product_id}) da WooCommerce NÃO encontrado na Agis. Ajustando estoque para 0.")
            if wc_stock > 0 or row['in_stock_wc']: # Se ele tinha estoque ou estava em estoque na WC
                new_stock = 0
                new_in_stock_status = False
                needs_update = True
        else:
            # Produto encontrado na Agis, compare e prepare a atualização
            if agis_price != wc_price:
                new_price = agis_price
                needs_update = True
                
            if agis_stock != wc_stock:
                new_stock = agis_stock
                new_in_stock_status = (new_stock > 0)
                needs_update = True
            
            # Se o status de estoque mudou (por exemplo, de em estoque para fora de estoque ou vice-versa)
            if (wc_stock > 0 and new_stock == 0) or (wc_stock == 0 and new_stock > 0):
                needs_update = True
        
        if needs_update:
            products_to_update_count += 1
            update_data = {
                "id": product_id,
                "regular_price": str(f"{new_price:.2f}"), # Garante formato de string com 2 casas decimais
                "stock_quantity": int(new_stock),
                "manage_stock": True, # Mantém gerenciamento de estoque ativo
                "in_stock": new_in_stock_status
            }
            updates_payload["update"].append(update_data)
            print(f"   ✏️ Programado: SKU '{sku}' (ID: {product_id}) | Preço: WC {wc_price:.2f} -> Agis {new_price:.2f} | Estoque: WC {wc_stock} -> Agis {new_stock}")


    if not updates_payload["update"]:
        print("\n✅ Nenhuma atualização de preço ou estoque necessária para os produtos gerenciados.")
        return

    print(f"\n📦 Enviando {products_to_update_count} produtos em lote para atualização na WooCommerce...")

    # Divida as atualizações em lotes menores para a API da WooCommerce (limite recomendado: 100 itens por lote)
    batch_size = 100
    for i in range(0, len(updates_payload["update"]), batch_size):
        batch = updates_payload["update"][i:i + batch_size]
        
        try:
            response = wcapi.post("products/batch", {"update": batch})

            if response.status_code == 200:
                print(f"   ✅ Lote de {len(batch)} itens enviado com sucesso.")
            else:
                print(f"   ❌ Erro ao enviar lote de atualizações ({len(batch)} itens): {response.status_code} - {response.text}")
                # Logar mais detalhes do erro, se necessário
                error_response = response.json()
                if 'data' in error_response and 'details' in error_response['data']:
                    print(f"      Detalhes do erro: {error_response['data']['details']}")
            time.sleep(1) # Pequeno atraso entre os lotes
        except requests.exceptions.Timeout:
            print(f"⏰ Timeout ao enviar lote de atualizações. Tentando o próximo lote...")
            time.sleep(5) # Espera e tenta o próximo lote
            continue
        except requests.exceptions.RequestException as e:
            print(f"❌ Erro de requisição ao enviar lote de atualizações: {e}")
            break

    print("\n✅ Processo de atualização concluído!")

# --- Execução Principal ---
if __name__ == "__main__":
    update_products_in_bulk()
