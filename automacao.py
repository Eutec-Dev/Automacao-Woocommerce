import requests
import pandas as pd
from woocommerce import API
import time
import urllib.parse

# --- Configuração das APIs ---
# ... (suas configurações atuais permanecem as mesmas) ...

wcapi = API(
    url="https://eutec.com.br",
    consumer_key="ck_5506e564a1f28a33558e9da73b33823db3c15510",
    consumer_secret="cs_07393e037d36912181839d01905909d568448350",
    version="wc/v3",
    timeout=20
)

API_URL = "https://vendas.agis.com.br/rest/all/V1/agis/reseller/product/list"
TOKEN = "1cnl71wepg3cqhu3t2nys2jgkks68yng"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}"
}
# PARAMS para busca inicial em massa (ajuste pageSize conforme necessário)
AGIS_PARAMS = {
    "searchCriteria[currentPage]": 1,
    "searchCriteria[pageSize]": 1000 # Busque o máximo possível de uma vez
}

def get_all_agis_products():
    """Busca TODOS os produtos da API da Agis (paginado)."""
    all_agis_products = []
    page = 1
    while True:
        print(f"Buscando produtos da Agis - Página {page}...")
        AGIS_PARAMS["searchCriteria[currentPage]"] = page
        response = requests.get(API_URL, headers=HEADERS, params=AGIS_PARAMS)

        if response.status_code != 200:
            print(f"Erro ao buscar produtos da Agis: {response.text}")
            break

        data = response.json()
        if not data or "items" not in data or not data["items"]:
            break

        all_agis_products.extend(data["items"])
        total_count = data.get("total_count", 0) # A API Agis pode retornar o total de itens
        
        # Verifique se já buscamos todas as páginas com base no total_count e pageSize
        if total_count > 0 and len(all_agis_products) >= total_count:
             break
        
        page += 1
        # É uma boa prática adicionar um pequeno atraso entre grandes lotes de requisições também
        time.sleep(0.5) 
        
    return all_agis_products

def get_all_woocommerce_products():
    """Busca TODOS os produtos com gerenciamento de estoque da API da WooCommerce (paginado)."""
    all_wc_products = []
    page = 1
    while True:
        print(f"Buscando produtos da WooCommerce - Página {page}...")
        # Adicione 'stock_status' para tentar filtrar produtos em estoque se possível
        response = wcapi.get("products", params={"per_page": 100, "page": page, "stock_status": "instock"}) 

        if response.status_code != 200:
            print(f"Erro ao buscar produtos da WooCommerce: {response.text}")
            break

        products = response.json()
        if not products:
            break
        
        # Filtra por produtos com gerenciamento de estoque habilitado e com SKU
        manageable_products = [p for p in products if p.get("manage_stock", False) and p.get("sku")]
        all_wc_products.extend(manageable_products)
        page += 1
        time.sleep(0.1) # Pequeno atraso para respeitar limites de taxa da WC

    return all_wc_products

def main():
    print("🔧 Iniciando atualização em massa...")

    # 1. Busque todos os dados relevantes da Agis (EM MASSA)
    agis_data = get_all_agis_products()
    if not agis_data:
        print("Nenhum dado da Agis encontrado. Saindo.")
        return

    # Processa os dados da Agis em um formato mais acessível (ex: dicionário por SKU)
    agis_skus_map = {}
    for item in agis_data:
        sku = item.get("sku", "").strip()
        if not sku:
            continue
        
        qty = 0
        price = 0
        for s in item.get("stock", []):
            if s.get("warehouse") in ["007", "004"]: # Inclui ambos os depósitos
                qty += s.get("qty", 0) # Soma os estoques
                # Se o preço for maior que zero, usa esse preço (você pode refinar a lógica de qual preço usar se houver múltiplos)
                if s.get("price", 0) > 0: 
                    price = s.get("price", 0) 
        
        # Aplica a margem se o preço for válido
        final_price = price / 0.80 if price > 0 else 0
        
        agis_skus_map[sku] = {"price": final_price, "qty": qty}

    # 2. Busque todos os produtos da WooCommerce (EM MASSA)
    wc_products = get_all_woocommerce_products()
    if not wc_products:
        print("Nenhum produto da WooCommerce encontrado para atualização. Saindo.")
        return

    # Prepare uma lista de atualizações em lote para a WooCommerce
    updates_to_send = {"update": []}
    
    # 3. Compare os dados e prepare as atualizações
    for product in wc_products:
        sku = product.get("sku", "").strip()
        wc_price = float(product.get("regular_price", 0)) if product.get("regular_price") else 0
        wc_stock = product.get("stock_quantity", 0)

        agis_info = agis_skus_map.get(sku)

        if agis_info:
            new_price = agis_info["price"]
            new_stock = agis_info["qty"]

            # Verifique se há necessidade de atualização para evitar chamadas desnecessárias
            if new_price != wc_price or new_stock != wc_stock:
                print(f"Programando atualização para SKU '{sku}': Preço WC {wc_price:.2f} -> Agis {new_price:.2f} | Estoque WC {wc_stock} -> Agis {new_stock}")
                updates_to_send["update"].append({
                    "id": product["id"],
                    "regular_price": str(new_price),
                    "stock_quantity": new_stock,
                    "manage_stock": True,
                    "in_stock": new_stock > 0
                })
        else:
            # Lida com SKUs da WooCommerce que não foram encontrados na Agis
            # Você pode optar por desativar o estoque, colocar em 0, ou apenas logar
            if wc_stock > 0 or product.get("in_stock", True): # Se o produto está em estoque mas não está na Agis, atualize
                print(f"[!] SKU '{sku}' da WooCommerce não encontrado na Agis. Definindo estoque para 0.")
                updates_to_send["update"].append({
                    "id": product["id"],
                    "stock_quantity": 0,
                    "manage_stock": True,
                    "in_stock": False
                })


    # 4. Envie as atualizações em lote para a WooCommerce
    if updates_to_send["update"]:
        print(f"\n📦 Enviando {len(updates_to_send['update'])} atualizações em lote para a WooCommerce...")
        # A API da WooCommerce pode ter um limite no número de itens por lote (ex: 100 itens)
        # Divida 'updates_to_send["update"]' em sublistas se for muito grande
        
        batch_size = 100 # WooCommerce batch update limit
        for i in range(0, len(updates_to_send["update"]), batch_size):
            batch = updates_to_send["update"][i:i + batch_size]
            
            payload = {"update": batch}
            response = wcapi.post("products/batch", payload) # Use POST para batch updates

            if response.status_code == 200:
                print(f"[✓] Lote de atualizações processado com sucesso (total: {len(batch)} itens).")
            else:
                print(f"[X] Erro ao enviar lote de atualizações: {response.text}")
            time.sleep(1) # Pequeno atraso entre os lotes
    else:
        print("\n▶ Nenhuma atualização necessária.")

    print("\n✅ Atualização concluída.")

# Execução principal
if __name__ == "__main__":
    main()
