import requests
import pandas as pd
from woocommerce import API
import time
import urllib.parse

# --- Configuração da API WooCommerce ---
# Aumentei o timeout para 30 segundos para ambas as APIs
wcapi = API(
    url="https://eutec.com.br",
    consumer_key="ck_5506e564a1f28a33558e9da73b33823db3c15510",
    consumer_secret="cs_07393e037d36912181839d01905909d568448350",
    version="wc/v3",
    timeout=30
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
    Busca TODOS os produtos da WooCommerce que têm 'manage_stock' ativo e status 'publish'.
    Retorna um DataFrame do Pandas.
    """
    all_wc_products_data = []
    page = 1
    per_page = 100 # Número de produtos por página

    print("🔧 Buscando produtos da WooCommerce com gerenciamento de estoque ativo...")
    while True:
        try:
            # Filtra por produtos com gerenciamento de estoque ativo E publicados
            response = wcapi.get("products", params={"per_page": per_page, "page": page, "manage_stock": "true", "status": "publish"})
            
            if response.status_code != 200:
                print(f"❌ Erro ao buscar produtos da WooCommerce (Página {page}): {response.status_code} - {response.text}")
                # Se for um 404, pode significar que não há mais páginas. Em outros erros, tenta de novo.
                if response.status_code == 404: 
                    break 
                time.sleep(5) 
                continue # Tenta a mesma página novamente após um atraso

            products = response.json()
            if not products:
                break # Sem mais produtos

            # Processa e adiciona apenas os dados relevantes
            for p in products:
                # Garante que 'stock_quantity' seja um número (int) ou 0
                stock_qty = p.get("stock_quantity") if p.get("stock_quantity") is not None else 0
                
                # Trata o preço regular que pode vir como None ou string vazia
                regular_price_str = p.get("regular_price") 
                regular_price_value = float(regular_price_str) if regular_price_str else 0.0 
                
                all_wc_products_data.append({
                    "id": p["id"],
                    "sku": str(p.get("sku", "")).strip(), # Garante que SKU é string e limpo
                    "regular_price_wc": regular_price_value,
                    "stock_quantity_wc": int(stock_qty),
                    "in_stock_wc": p.get("in_stock", False) # Salva o status de estoque atual da WC
                })
            
            print(f"    ✅ Página {page} da WooCommerce processada. Produtos encontrados com estoque gerenciado: {len(products)}")
            
            page += 1
            time.sleep(0.2) # Pequeno atraso para respeitar os limites de taxa da WC

        except requests.exceptions.Timeout:
            print(f"⏰ Timeout ao buscar produtos da WooCommerce na página {page}. Tentando novamente...")
            time.sleep(10) # Espera mais tempo e tenta novamente
            continue
        except requests.exceptions.ConnectionError as e:
            print(f"❌ Erro de conexão ao buscar produtos da WooCommerce na página {page}: {e}. Verifique sua rede/servidor.")
            time.sleep(15) 
            continue
        except requests.exceptions.RequestException as e:
            print(f"❌ Erro de requisição desconhecido ao buscar produtos da WooCommerce na página {page}: {e}")
            break # Erro genérico, pode ser melhor parar a execução

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
            response = requests.get(API_URL, headers=HEADERS, params=params, timeout=30) 
            
            if response.status_code != 200:
                print(f"❌ Erro ao buscar produtos da Agis (Página {page}): {response.status_code} - {response.text}")
                if response.status_code == 404: # Se for 404, não há mais dados
                    break
                time.sleep(5)
                continue # Tenta a mesma página novamente

            data = response.json()
            items = data.get("items", [])
            if not items:
                break # Sem mais itens

            for item in items:
                sku = str(item.get("sku", "")).strip() # Garante que SKU é string e limpo
                if not sku: # Pula se o SKU for vazio (inválido para matching)
                    continue

                qty = 0
                price = 0
                
                # Soma os estoques dos armazéns 007 e 004 e pega o preço válido
                for s in item.get("stock", []):
                    if s.get("warehouse") in ["007", "004"]:
                        qty += s.get("qty", 0)
                        # Tenta converter o preço para float, se não for um número válido, usa 0.0
                        current_price_agis = float(str(s.get("price", 0)).replace(',', '.')) if str(s.get("price", 0)).replace('.', '', 1).isdigit() else 0.0
                        if current_price_agis > 0:  # Assume que se houver múltiplos preços, você quer o último encontrado > 0
                            price = current_price_agis 

                # Aplica a margem se o preço for válido
                final_price = price / 0.80 if price > 0 else 0
                all_agis_products_data.append({"sku": sku, "agis_price": final_price, "agis_stock": qty})
            
            print(f"    ✅ Página {page} da Agis processada. Itens encontrados: {len(items)}")

            total_count = data.get("total_count", 0)
            # Verifica se já buscamos tudo com base no total_count da API
            if total_count > 0 and len(all_agis_products_data) >= total_count:
                print(f"    Total de produtos Agis a buscar atingido ({total_count}).")
                break 

            page += 1
            time.sleep(0.5) # Pequeno atraso entre as requisições em lote da Agis

        except requests.exceptions.Timeout:
            print(f"⏰ Timeout ao buscar produtos da Agis na página {page}. Tentando novamente...")
            time.sleep(10) 
            continue
        except requests.exceptions.ConnectionError as e:
            print(f"❌ Erro de conexão ao buscar produtos da Agis na página {page}: {e}. Verifique sua rede/servidor.")
            time.sleep(15)
            continue
        except requests.exceptions.RequestException as e:
            print(f"❌ Erro de requisição desconhecido ao buscar produtos da Agis na página {page}: {e}")
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
        print("Nenhum produto da WooCommerce com estoque gerenciado para processar. Processo encerrado.")
        return

    # 2. Obter todos os produtos da Agis
    df_agis = get_all_agis_products()
    if df_agis.empty:
        print("Nenhum dado da Agis para comparar. Verifique a API Agis. Processo encerrado.")
        return

    # 3. Criar a tabela de produtos para comparação (JOIN dos DataFrames)
    df_merged = pd.merge(df_wc, df_agis, on='sku', how='left')

    print("\n📊 Tabela de produtos para comparação criada. Identificando atualizações necessárias...")

    updates_payload = {"update": []}
    products_to_update_count = 0

    # Defina o limite de preço para a regra de filtro da Agis
    PRICE_THRESHOLD = 400.00

    # Itera sobre o DataFrame mesclado para identificar as mudanças
    for index, row in df_merged.iterrows():
        product_id = row['id']
        sku = row['sku']
        wc_price = round(row['regular_price_wc'], 2) # Arredonda para 2 casas para comparação
        wc_stock = int(row['stock_quantity_wc'])
        wc_in_stock = row['in_stock_wc']
        
        # O Pandas usa pd.isna() para verificar valores ausentes (NaN) após um left merge
        agis_price_found = not pd.isna(row['agis_price'])
        agis_stock_found = not pd.isna(row['agis_stock'])

        # --- CORREÇÃO: Inicializar agis_price e agis_stock antes do bloco condicional ---
        agis_price = 0.0 # Valor padrão para caso não seja encontrado na Agis
        agis_stock = 0   # Valor padrão para caso não seja encontrado na Agis

        new_price = wc_price # Inicializa com o preço atual da WC
        new_stock = wc_stock # Inicializa com o estoque atual da WC
        new_in_stock_status = wc_in_stock # Inicializa com o status de estoque atual da WC
        needs_update = False

        if agis_price_found and agis_stock_found:
            agis_price = round(row['agis_price'], 2) # Atribui o valor real da Agis
            agis_stock = int(row['agis_stock'])     # Atribui o valor real da Agis

            # --- Lógica principal para filtro de preço e estoque com base na Agis ---
            if agis_price <= PRICE_THRESHOLD:
                # Regra: Se o produto na Agis custa <= R$400, zerar estoque na WC e NÃO ATUALIZAR PREÇO.
                print(f"    🚫 SKU '{sku}' (ID: {product_id}): Preço da Agis ({agis_price:.2f}) é <= R${PRICE_THRESHOLD:.2f}. PREÇO NÃO SERÁ ATUALIZADO.")
                if wc_stock > 0 or wc_in_stock: # Só atualiza se o estoque atual não for já 0/fora de estoque
                    new_stock = 0
                    new_in_stock_status = False
                    needs_update = True
                    print(f"      -> ESTOQUE PROGRAMADO PARA 0 e 'fora de estoque' na WooCommerce.")
                # O new_price permanece o wc_price (ou seja, não muda)
                
            else: # agis_price > PRICE_THRESHOLD (preço maior que R$400,00 na Agis)
                # Regra: Se o produto na Agis custa > R$400,00, sincronizar preço e estoque normalmente.
                if agis_price != wc_price:
                    new_price = agis_price
                    needs_update = True
                    print(f"    ✏️ PREÇO Programado para SKU '{sku}' (ID: {product_id}): WC {wc_price:.2f} -> Agis {new_price:.2f}")
                
                if agis_stock != wc_stock:
                    new_stock = agis_stock
                    needs_update = True
                    print(f"    ✏️ ESTOQUE Programado para SKU '{sku}' (ID: {product_id}): WC {wc_stock} -> Agis {new_stock}")
                
                # O status 'in_stock' deve refletir a nova quantidade de estoque (True se new_stock > 0, False caso contrário)
                expected_in_stock_status = (new_stock > 0)
                if expected_in_stock_status != wc_in_stock: # Se o status de estoque mudou
                    new_in_stock_status = expected_in_stock_status
                    needs_update = True
                    print(f"    ✏️ STATUS ESTOQUE Programado para SKU '{sku}' (ID: {product_id}): WC {wc_in_stock} -> Novo: {new_in_stock_status}")

        else:
            # SKU da WooCommerce não encontrado na Agis. Define estoque como 0 na WC.
            print(f"    [!] SKU '{sku}' (ID: {product_id}) da WooCommerce NÃO encontrado na Agis.")
            # Só atualiza se o estoque atual for > 0 ou se estiver marcado como 'em estoque'
            if wc_stock > 0 or wc_in_stock: 
                new_stock = 0
                new_in_stock_status = False # Marca como fora de estoque
                needs_update = True
                print(f"      -> Ajustando estoque para 0 e 'fora de estoque' na WooCommerce para SKU '{sku}'.")
        
        if needs_update:
            products_to_update_count += 1
            update_data = {
                "id": product_id,
                "stock_quantity": int(new_stock),
                "manage_stock": True, # Mantém gerenciamento de estoque ativo
                "in_stock": new_in_stock_status
            }
            
            # Adiciona 'regular_price' ao payload SOMENTE se 'new_price' foi de fato alterado
            # E não estamos no cenário onde o preço da Agis é <= PRICE_THRESHOLD (onde o preço não deve ser atualizado)
            # ou se era 0 na WC e agora o Agis_price > 400
            if new_price != wc_price or (agis_price_found and agis_price > PRICE_THRESHOLD and wc_price == 0.0):
                update_data['regular_price'] = str(f"{new_price:.2f}")
            else:
                # Se o preço não foi alterado ou se a regra de <=400 da Agis o manteve inalterado,
                # remove 'regular_price' do payload para não forçar uma atualização desnecessária ou incorreta.
                update_data.pop('regular_price', None)


            updates_payload["update"].append(update_data)
            
            # Ajuste na mensagem de log para refletir se o preço foi atualizado ou não
            price_log_str = f"Preço: WC {wc_price:.2f} -> Agis {new_price:.2f}" if 'regular_price' in update_data else f"Preço WC: {wc_price:.2f} (permanece)"
            stock_log_str = f"Estoque: WC {wc_stock} -> Agis {new_stock}"
            status_log_str = f"Em Estoque WC: {wc_in_stock} -> Novo: {new_in_stock_status}"
            print(f"    ✅ PROGRAMADO PARA ATUALIZAÇÃO: SKU '{sku}' (ID: {product_id}) | {price_log_str} | {stock_log_str} | {status_log_str}")


    if not updates_payload["update"]:
        print("\n✅ Nenhuma atualização de preço ou estoque necessária para os produtos gerenciados, considerando as regras de R$400. Processo encerrado.")
        return

    print(f"\n📦 Enviando {products_to_update_count} produtos em lote para atualização na WooCommerce...")

    # Divida as atualizações em lotes menores para a API da WooCommerce (limite recomendado: 100 itens por lote)
    batch_size = 100
    for i in range(0, len(updates_payload["update"]), batch_size):
        batch = updates_payload["update"][i:i + batch_size]
        
        try:
            response = wcapi.post("products/batch", {"update": batch})

            if response.status_code == 200:
                print(f"    ✅ Lote de {len(batch)} itens enviado com sucesso.")
            else:
                # Loga mais detalhes do erro para depuração
                print(f"    ❌ Erro ao enviar lote de atualizações ({len(batch)} itens): {response.status_code} - {response.text}")
                error_response = response.json()
                if 'data' in error_response and 'details' in error_response['data']:
                    print(f"      Detalhes do erro: {error_response['data']['details']}")
                # Pode haver erros específicos por item no lote
                if 'errors' in error_response:
                    for error_item in error_response['errors']:
                        print(f"        Erro no item {error_item.get('id')} ({error_item.get('code')}): {error_item.get('message')}")

            time.sleep(1) # Pequeno atraso entre os lotes
        except requests.exceptions.Timeout:
            print(f"⏰ Timeout ao enviar lote de atualizações. Tentando o próximo lote...")
            time.sleep(5) 
            continue
        except requests.exceptions.ConnectionError as e:
            print(f"❌ Erro de conexão ao enviar lote de atualizações: {e}. Verifique sua rede/servidor.")
            time.sleep(10)
            continue
        except requests.exceptions.RequestException as e:
            print(f"❌ Erro de requisição desconhecido ao enviar lote de atualizações: {e}")
            break

    print("\n✅ Processo de atualização concluído!")

# --- Execução Principal ---
if __name__ == "__main__":
    update_products_in_bulk()
