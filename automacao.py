import requests
import pandas as pd
from woocommerce import API
import time
import sys
import urllib.parse
import os

# ─── Configuração da API WooCommerce ────────────────────────────────────────
wcapi = API(
    url="https://eutec.com.br",
    consumer_key=os.environ["WC_CONSUMER_KEY"],
    consumer_secret=os.environ["WC_CONSUMER_SECRET"],
    version="wc/v3",
    timeout=30
)

# ─── Configuração da API Agis ────────────────────────────────────────────────
API_URL = "https://vendas.agis.com.br/rest/all/V1/agis/reseller/product/list"
TOKEN = os.environ["AGIS_TOKEN"]
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}",
    "User-Agent": "EUTEC-Sync/2.0"
}

# ─── Parâmetros ──────────────────────────────────────────────────────────────
MAX_RETRIES          = 3      # Tentativas por requisição antes de abortar
MAX_CONSECUTIVE_ERRS = 5      # Circuit breaker: erros seguidos
BACKOFF_BASE         = 2      # Backoff exponencial: 2s, 4s, 8s...
WC_PAGE_DELAY        = 0.3    # Delay entre páginas da WooCommerce
AGIS_BATCH_DELAY     = 3.0    # Delay entre lotes de SKUs enviados à Agis
AGIS_SKU_BATCH_SIZE  = 25     # Quantidade de SKUs por requisição à Agis
MARGIN_DIVISOR       = 0.97   # Margem do Acyr: preço_agis / 0.97
WAREHOUSES           = ["007", "004"]  # Armazéns físicos considerados


# ─── Circuit Breaker ─────────────────────────────────────────────────────────
class CircuitBreaker:
    def __init__(self, max_errors: int):
        self.errors = 0
        self.max_errors = max_errors

    def success(self):
        self.errors = 0

    def fail(self, context: str = ""):
        self.errors += 1
        print(f"    ⚠️  Erro consecutivo {self.errors}/{self.max_errors} ({context})")
        if self.errors >= self.max_errors:
            print(
                "\n🔴 CIRCUIT BREAKER ATIVADO: muitos erros consecutivos.\n"
                "   Abortando para proteger a API da Agis.\n"
                "   Verifique conectividade ou se a API está em manutenção."
            )
            sys.exit(2)


circuit = CircuitBreaker(MAX_CONSECUTIVE_ERRS)


# ─── Helper: trata erros HTTP ────────────────────────────────────────────────
def handle_http_error(status_code: int, resp_headers: dict, source: str, attempt: int) -> str:
    """
    Retorna ação: 'abort', 'rate_limit', 'retry', 'not_found'.
    503 e 401/403 abortam imediatamente — nunca fazem retry.
    """
    if status_code == 503:
        print(
            f"\n🔴 {source} retornou 503 (API em manutenção).\n"
            f"   Abortando IMEDIATAMENTE. Não tente novamente até a API voltar."
        )
        sys.exit(1)

    if status_code in [401, 403]:
        print(f"\n🔴 {source} retornou {status_code} (token inválido ou acesso negado). Abortando.")
        sys.exit(1)

    if status_code == 429:
        retry_after = int(resp_headers.get("Retry-After", 60))
        print(f"    ⏳ Rate limit (429) em {source}. Aguardando {retry_after}s...")
        time.sleep(retry_after)
        return "rate_limit"

    if status_code == 404:
        return "not_found"

    wait = BACKOFF_BASE ** attempt
    print(f"    ❌ {source} erro {status_code}. Tentativa {attempt}/{MAX_RETRIES}. Aguardando {wait}s...")
    circuit.fail(f"HTTP {status_code} em {source}")
    time.sleep(wait)
    return "retry"


# ─── Requisição com retry ─────────────────────────────────────────────────────
def safe_get(url: str, params: dict, source: str) -> dict | None:
    """
    Faz GET com backoff exponencial e limite de tentativas.
    Retorna o JSON parseado ou None se 404 (sem dados).
    Aborta com sys.exit em erros críticos.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=30)

            if response.status_code == 200:
                circuit.success()
                return response.json()

            action = handle_http_error(response.status_code, response.headers, source, attempt)

            if action == "not_found":
                return None
            # rate_limit e retry: continua o loop

        except requests.exceptions.Timeout:
            wait = BACKOFF_BASE ** attempt
            print(f"    ⏰ Timeout em {source}. Tentativa {attempt}/{MAX_RETRIES}. Aguardando {wait}s...")
            circuit.fail(f"Timeout {source}")
            time.sleep(wait)

        except requests.exceptions.ConnectionError:
            wait = BACKOFF_BASE ** attempt
            print(f"    ❌ Erro de conexão em {source}. Tentativa {attempt}/{MAX_RETRIES}. Aguardando {wait}s...")
            circuit.fail(f"ConnectionError {source}")
            time.sleep(wait)

        except requests.exceptions.RequestException as e:
            print(f"    ❌ Erro inesperado em {source}: {e}. Abortando.")
            sys.exit(1)

    print(f"\n🔴 {source}: falhou após {MAX_RETRIES} tentativas. Abortando para não pressionar a API.")
    sys.exit(1)


# ─── PASSO 1: Busca produtos da EUTEC (WooCommerce) ─────────────────────────
def get_eutec_products() -> pd.DataFrame:
    """
    Busca todos os produtos publicados com manage_stock=true na EUTEC.
    Retorna DataFrame com: id, sku, regular_price_wc, stock_quantity_wc,
                           in_stock_wc, manage_stock_wc
    """
    all_products = []
    page = 1

    print("─" * 60)
    print("📦 PASSO 1: Buscando produtos da EUTEC (WooCommerce)...")
    print("─" * 60)

    while True:
        success = False

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = wcapi.get(
                    "products",
                    params={
                        "per_page": 100,
                        "page": page,
                        "manage_stock": "true",
                        "status": "publish"
                    }
                )

                if response.status_code == 200:
                    products = response.json()
                    if not products:
                        circuit.success()
                        print(f"\n☑️  Total EUTEC: {len(all_products)} produtos com estoque gerenciado.")
                        return pd.DataFrame(all_products) if all_products else pd.DataFrame()

                    for p in products:
                        sku = str(p.get("sku", "")).strip()
                        if not sku:
                            continue  # SKU vazio não pode ser buscado na Agis
                        stock_qty = p.get("stock_quantity") or 0
                        price_str = p.get("regular_price") or "0"
                        all_products.append({
                            "id":               p["id"],
                            "sku":              sku,
                            "regular_price_wc": float(price_str) if price_str else 0.0,
                            "stock_quantity_wc":int(stock_qty),
                            "in_stock_wc":      p.get("in_stock", False),
                            "manage_stock_wc":  p.get("manage_stock", False)
                        })

                    print(f"    ✅ Página {page}: {len(products)} produtos")
                    circuit.success()
                    success = True
                    page += 1
                    time.sleep(WC_PAGE_DELAY)
                    break

                action = handle_http_error(response.status_code, response.headers, "WooCommerce", attempt)
                if action == "not_found":
                    return pd.DataFrame(all_products) if all_products else pd.DataFrame()

            except requests.exceptions.Timeout:
                wait = BACKOFF_BASE ** attempt
                print(f"    ⏰ Timeout WC página {page}. Tentativa {attempt}/{MAX_RETRIES}. Aguardando {wait}s...")
                circuit.fail("Timeout WC")
                time.sleep(wait)

            except requests.exceptions.ConnectionError:
                wait = BACKOFF_BASE ** attempt
                print(f"    ❌ Conexão WC página {page}. Tentativa {attempt}/{MAX_RETRIES}. Aguardando {wait}s...")
                circuit.fail("ConnectionError WC")
                time.sleep(wait)

            except requests.exceptions.RequestException as e:
                print(f"    ❌ Erro inesperado WC: {e}. Abortando.")
                sys.exit(1)

        if not success:
            print(f"\n🔴 WooCommerce: página {page} falhou após {MAX_RETRIES} tentativas. Abortando.")
            sys.exit(1)


# ─── PASSO 2: Busca na Agis SOMENTE os SKUs da EUTEC ────────────────────────
def get_agis_data_for_skus(skus: list[str]) -> dict:
    """
    Busca na API da Agis apenas os SKUs presentes na EUTEC.
    Envia em lotes de AGIS_SKU_BATCH_SIZE usando o filtro 'in' do searchCriteria.
    Retorna dict: { sku_upper: {"agis_price": float, "agis_stock": int} }

    Estratégia de lote:
    - Usa conditionType 'in' com múltiplos SKUs separados por vírgula.
    - Isso é 1 requisição por lote (ex: 50 SKUs = 1 req), muito mais eficiente
      do que 1 requisição por SKU e sem varrer o catálogo inteiro da Agis.
    """
    result = {}
    total = len(skus)
    batches = [skus[i:i + AGIS_SKU_BATCH_SIZE] for i in range(0, total, AGIS_SKU_BATCH_SIZE)]

    print("─" * 60)
    print(f"🔍 PASSO 2: Buscando {total} SKUs da EUTEC na Agis ({len(batches)} lotes de até {AGIS_SKU_BATCH_SIZE})...")
    print("─" * 60)

    for batch_num, batch in enumerate(batches, 1):
        skus_value = ",".join(batch)

        params = {
            "searchCriteria[filterGroups][0][filters][0][field]":         "SKU",
            "searchCriteria[filterGroups][0][filters][0][value]":         skus_value,
            "searchCriteria[filterGroups][0][filters][0][conditionType]": "in",
            "searchCriteria[pageSize]":                                    AGIS_SKU_BATCH_SIZE,
            "searchCriteria[currentPage]":                                 1,
        }

        print(f"    📡 Lote {batch_num}/{len(batches)}: {len(batch)} SKUs...")
        data = safe_get(API_URL, params, f"Agis lote {batch_num}")

        if data is None:
            print(f"    ⚠️  Lote {batch_num}: nenhum item retornado (404).")
        else:
            items = data.get("items", [])
            found = 0
            for item in items:
                sku = str(item.get("sku", "")).strip().upper()
                if not sku:
                    continue

                qty = 0
                price = 0.0
                for s in item.get("stock", []):
                    if s.get("warehouse") in WAREHOUSES:
                        qty += s.get("qty", 0)
                        raw = str(s.get("price", 0)).replace(",", ".")
                        p = float(raw) if raw.replace(".", "", 1).isdigit() else 0.0
                        if p > 0:
                            price = p

                # Aplica a margem do Acyr
                final_price = round(price / MARGIN_DIVISOR, 2) if price > 0 else 0.0
                result[sku] = {"agis_price": final_price, "agis_stock": int(qty)}
                found += 1

            print(f"    ✅ Lote {batch_num}: {found} produtos encontrados na Agis.")

        time.sleep(AGIS_BATCH_DELAY)

    print(f"\n☑️  Total encontrado na Agis: {len(result)} de {total} SKUs da EUTEC.")
    return result


# ─── PASSO 3: Compara e monta payload de atualização ────────────────────────
def build_update_payload(df_wc: pd.DataFrame, agis_data: dict) -> list:
    """
    Compara dados da WooCommerce com os da Agis e monta lista de atualizações.
    Regras:
    - SKU não encontrado na Agis → zera estoque na WC
    - SKU encontrado, estoque ou preço diferente → atualiza
    - Preço final = agis_price (já com margem / 0.97 aplicada)
    """
    updates = []

    print("─" * 60)
    print("📊 PASSO 3: Comparando dados e montando atualizações...")
    print("─" * 60)

    for _, row in df_wc.iterrows():
        product_id      = row["id"]
        sku             = row["sku"]
        wc_price        = round(row["regular_price_wc"], 2)
        wc_stock        = int(row["stock_quantity_wc"])
        wc_in_stock     = row["in_stock_wc"]
        wc_manage_stock = row["manage_stock_wc"]

        if not wc_manage_stock:
            print(f"    ⏭️  SKU '{sku}' sem gerenciamento de estoque. Ignorando.")
            continue

        agis = agis_data.get(sku.upper())

        new_price      = wc_price
        new_stock      = wc_stock
        new_in_stock   = wc_in_stock
        needs_update   = False
        price_changed  = False

        if agis is None:
            # ── Produto não existe na Agis → zera estoque ──
            print(f"    [!] SKU '{sku}' (ID:{product_id}) não encontrado na Agis → zerando estoque.")
            if wc_stock > 0 or wc_in_stock:
                new_stock    = 0
                new_in_stock = False
                needs_update = True

        else:
            agis_price = agis["agis_price"]
            agis_stock = agis["agis_stock"]

            # Preço
            if agis_price > 0 and agis_price != wc_price:
                new_price    = agis_price
                needs_update = True
                price_changed = True
                print(f"    ✏️  PREÇO '{sku}' (ID:{product_id}): R${wc_price:.2f} → R${new_price:.2f}")

            # Estoque
            if agis_stock != wc_stock:
                new_stock    = agis_stock
                needs_update = True
                print(f"    ✏️  ESTOQUE '{sku}' (ID:{product_id}): {wc_stock} → {new_stock}")

            # Status in_stock
            expected_in_stock = new_stock > 0
            if expected_in_stock != wc_in_stock:
                new_in_stock = expected_in_stock
                needs_update = True
                print(f"    ✏️  STATUS '{sku}' (ID:{product_id}): {wc_in_stock} → {new_in_stock}")

        if needs_update:
            update_data = {
                "id":             product_id,
                "stock_quantity": int(new_stock),
                "in_stock":       new_in_stock,
                "manage_stock":   True,
            }
            if price_changed:
                update_data["regular_price"] = f"{new_price:.2f}"

            updates.append(update_data)

    return updates


# ─── PASSO 4: Envia atualizações em lote para WooCommerce ───────────────────
def send_updates_to_woocommerce(updates: list):
    """
    Envia as atualizações em lotes de 100 para a API batch da WooCommerce.
    """
    total = len(updates)
    print("─" * 60)
    print(f"📤 PASSO 4: Enviando {total} atualizações para WooCommerce...")
    print("─" * 60)

    batch_size = 100
    batches = [updates[i:i + batch_size] for i in range(0, total, batch_size)]

    for batch_num, batch in enumerate(batches, 1):
        print(f"    📡 Lote {batch_num}/{len(batches)}: {len(batch)} produtos...")

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = wcapi.post("products/batch", {"update": batch})

                if response.status_code == 200:
                    print(f"    ✅ Lote {batch_num} enviado com sucesso.")
                    circuit.success()
                    break
                else:
                    wait = BACKOFF_BASE ** attempt
                    print(f"    ❌ Erro {response.status_code} no lote {batch_num}. Tentativa {attempt}/{MAX_RETRIES}. Aguardando {wait}s...")
                    circuit.fail(f"Batch HTTP {response.status_code}")
                    time.sleep(wait)

            except requests.exceptions.Timeout:
                wait = BACKOFF_BASE ** attempt
                print(f"    ⏰ Timeout lote {batch_num}. Tentativa {attempt}/{MAX_RETRIES}. Aguardando {wait}s...")
                circuit.fail("Timeout Batch")
                time.sleep(wait)

            except requests.exceptions.ConnectionError:
                wait = BACKOFF_BASE ** attempt
                print(f"    ❌ Conexão lote {batch_num}. Tentativa {attempt}/{MAX_RETRIES}. Aguardando {wait}s...")
                circuit.fail("ConnectionError Batch")
                time.sleep(wait)

            except requests.exceptions.RequestException as e:
                print(f"    ❌ Erro inesperado lote {batch_num}: {e}. Pulando lote.")
                break

        time.sleep(1)


# ─── Execução Principal ───────────────────────────────────────────────────────
def main():
    print("\n🚀 Iniciando sincronização EUTEC ↔ Agis\n")

    # Passo 1: pega produtos da EUTEC
    df_wc = get_eutec_products()
    if df_wc.empty:
        print("⚠️  Nenhum produto com gerenciamento de estoque na EUTEC. Encerrando.")
        return

    # Passo 2: busca SOMENTE os SKUs da EUTEC na Agis
    skus_eutec = df_wc["sku"].dropna().unique().tolist()
    agis_data = get_agis_data_for_skus(skus_eutec)

    # Passo 3: compara e monta atualizações
    updates = build_update_payload(df_wc, agis_data)

    if not updates:
        print("\n✅ Nenhuma atualização necessária. Tudo sincronizado!")
        return

    # Passo 4: envia para WooCommerce
    send_updates_to_woocommerce(updates)

    print("\n✅ Sincronização concluída!")
    print(f"   Produtos EUTEC:          {len(df_wc)}")
    print(f"   Encontrados na Agis:     {len(agis_data)}")
    print(f"   Atualizações enviadas:   {len(updates)}")


if __name__ == "__main__":
    main()
