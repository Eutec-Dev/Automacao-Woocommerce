import requests
import pandas as pd
from woocommerce import API
import time
import sys
import os

sys.stdout.reconfigure(line_buffering=True)

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
AGIS_SKU_BATCH_SIZE  = 50     # Quantidade de SKUs por requisição à Agis
MARGIN_DIVISOR       = 0.96   # Margem do Acyr: preço_agis / 0.96
PRICE_ADDON_LIMIT    = 3000.0 # Produtos com preço final (após /0.96) <= este valor...
PRICE_ADDON_VALUE    = 50.0   # ...recebem este acréscimo em R$
WAREHOUSES           = ["007", "004"]  # Armazéns considerados, em ORDEM DE PRIORIDADE de preço
MIN_FOUND_RATIO      = 0.05   # Trava de sanidade: aborta se a Agis devolver < 5% dos SKUs
DRY_RUN              = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
WATCH_SKUS           = {s.strip().upper() for s in os.environ.get("WATCH_SKUS", "").split(",") if s.strip()}


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
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=30)

            if response.status_code == 200:
                circuit.success()
                return response.json()

            action = handle_http_error(response.status_code, response.headers, source, attempt)
            if action == "not_found":
                return None

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


# ─── Helpers de conversão ────────────────────────────────────────────────────
def to_float(value) -> float:
    """Converte preço da Agis/WC para float sem quebrar com vírgula, vazio ou None."""
    if value is None:
        return 0.0
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return 0.0


def to_int(value) -> int:
    try:
        return int(float(value or 0))
    except (ValueError, TypeError):
        return 0


# ─── Regra de preço ──────────────────────────────────────────────────────────
def calc_final_price(agis_price: float) -> float:
    """
    preço_agis / MARGIN_DIVISOR e, se o resultado ficar <= PRICE_ADDON_LIMIT,
    soma PRICE_ADDON_VALUE. Preço 0 continua 0 (não gera preço de R$ 50).
    """
    if agis_price <= 0:
        return 0.0
    final = round(agis_price / MARGIN_DIVISOR, 2)
    if final <= PRICE_ADDON_LIMIT:
        final = round(final + PRICE_ADDON_VALUE, 2)
    return final


# ─── PASSO 1: Busca produtos da EUTEC (WooCommerce) ─────────────────────────
def get_eutec_products() -> pd.DataFrame:
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
                            continue
                        # wc/v3 não aceita filtro manage_stock na listagem → filtra aqui
                        if not p.get("manage_stock", False):
                            continue
                        all_products.append({
                            "id":                p["id"],
                            "sku":               sku,
                            "regular_price_wc":  to_float(p.get("regular_price")),
                            "stock_quantity_wc": to_int(p.get("stock_quantity")),
                            # FIX: wc/v3 usa stock_status, não in_stock
                            "in_stock_wc":       p.get("stock_status") == "instock",
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
def extract_agis_stock_price(item: dict) -> tuple[int, float]:
    """
    Soma o estoque dos armazéns em WAREHOUSES e pega o preço seguindo a
    ORDEM DE PRIORIDADE de WAREHOUSES (007 antes de 004), só de armazém com preço > 0.
    """
    by_wh = {}
    for s in item.get("stock", []) or []:
        wh = str(s.get("warehouse", "")).strip()
        by_wh[wh] = {"qty": to_int(s.get("qty")), "price": to_float(s.get("price"))}

    qty = sum(by_wh.get(wh, {}).get("qty", 0) for wh in WAREHOUSES)

    price = 0.0
    # 1ª tentativa: armazém prioritário que tenha estoque e preço
    for wh in WAREHOUSES:
        d = by_wh.get(wh)
        if d and d["qty"] > 0 and d["price"] > 0:
            price = d["price"]
            break
    # 2ª tentativa: qualquer armazém considerado com preço, mesmo sem estoque
    if price == 0.0:
        for wh in WAREHOUSES:
            d = by_wh.get(wh)
            if d and d["price"] > 0:
                price = d["price"]
                break

    return qty, price


def get_agis_data_for_skus(skus: list[str]) -> dict:
    result = {}
    total = len(skus)
    batches = [skus[i:i + AGIS_SKU_BATCH_SIZE] for i in range(0, total, AGIS_SKU_BATCH_SIZE)]

    print("─" * 60)
    print(f"🔍 PASSO 2: Buscando {total} SKUs da EUTEC na Agis ({len(batches)} lotes de até {AGIS_SKU_BATCH_SIZE})...")
    print("─" * 60)

    for batch_num, batch in enumerate(batches, 1):
        params = {
            "searchCriteria[filterGroups][0][filters][0][field]":         "SKU",
            "searchCriteria[filterGroups][0][filters][0][value]":         ",".join(batch),
            "searchCriteria[filterGroups][0][filters][0][conditionType]": "in",
            "searchCriteria[pageSize]":                                    500,
            "searchCriteria[currentPage]":                                 1,
        }

        data = safe_get(API_URL, params, f"Agis lote {batch_num}")

        if data is None:
            print(f"    ⚠️  Lote {batch_num}/{len(batches)}: nenhum item retornado (404).")
        else:
            items = data.get("items", []) or []
            found = 0
            for item in items:
                sku = str(item.get("sku", "")).strip().upper()
                if not sku:
                    continue

                qty, price = extract_agis_stock_price(item)
                final_price = calc_final_price(price)
                result[sku] = {"agis_price": final_price, "agis_stock": qty, "agis_raw_price": price}
                found += 1

                if sku in WATCH_SKUS:
                    addon = " (+R$ acréscimo)" if 0 < round(price / MARGIN_DIVISOR, 2) <= PRICE_ADDON_LIMIT else ""
                    print(f"    👀 WATCH {sku}: stock={item.get('stock')} → qty={qty}, preço={price} → final={final_price}{addon}")

            print(f"    ✅ Lote {batch_num}/{len(batches)}: {found}/{len(batch)} SKUs encontrados na Agis.")

        time.sleep(AGIS_BATCH_DELAY)

    print(f"\n☑️  Total encontrado na Agis: {len(result)} de {total} SKUs da EUTEC.")
    return result


# ─── PASSO 3: Compara e monta payload de atualização ────────────────────────
def build_update_payload(df_wc: pd.DataFrame, agis_data: dict) -> list:
    updates = []
    stats = {"preco": 0, "estoque": 0, "status": 0, "zerados": 0}

    print("─" * 60)
    print("📊 PASSO 3: Comparando dados e montando atualizações...")
    print("─" * 60)

    for _, row in df_wc.iterrows():
        product_id  = int(row["id"])
        sku         = row["sku"]
        wc_price    = round(float(row["regular_price_wc"]), 2)
        wc_stock    = int(row["stock_quantity_wc"])
        wc_in_stock = bool(row["in_stock_wc"])

        agis = agis_data.get(sku.upper())

        new_price     = wc_price
        new_stock     = wc_stock
        price_changed = False

        if agis is None:
            if wc_stock != 0 or wc_in_stock:
                new_stock = 0
                stats["zerados"] += 1
                print(f"    [!] SKU '{sku}' (ID:{product_id}) não encontrado na Agis → zerando estoque.")
        else:
            agis_price = agis["agis_price"]
            agis_stock = agis["agis_stock"]

            # Preço: compara em centavos para evitar falso "diferente" por float
            if agis_price > 0 and round(agis_price * 100) != round(wc_price * 100):
                new_price     = agis_price
                price_changed = True
                stats["preco"] += 1
                print(f"    ✏️  PREÇO '{sku}' (ID:{product_id}): R${wc_price:.2f} → R${new_price:.2f}")

            if agis_stock != wc_stock:
                new_stock = agis_stock
                stats["estoque"] += 1
                print(f"    ✏️  ESTOQUE '{sku}' (ID:{product_id}): {wc_stock} → {new_stock}")

            if agis_price == 0 and agis_stock > 0:
                print(f"    ⚠️  SKU '{sku}' (ID:{product_id}) tem estoque {agis_stock} mas preço 0 na Agis — preço mantido.")

        new_in_stock = new_stock > 0
        status_changed = new_in_stock != wc_in_stock
        if status_changed:
            stats["status"] += 1
            print(f"    ✏️  STATUS '{sku}' (ID:{product_id}): {wc_in_stock} → {new_in_stock}")

        if price_changed or new_stock != wc_stock or status_changed:
            update_data = {
                "id":             product_id,
                "manage_stock":   True,
                "stock_quantity": int(new_stock),
                # FIX: wc/v3 usa stock_status
                "stock_status":   "instock" if new_in_stock else "outofstock",
            }
            if price_changed:
                update_data["regular_price"] = f"{new_price:.2f}"
            updates.append(update_data)

    print(
        f"\n☑️  Mudanças: {stats['preco']} preço | {stats['estoque']} estoque | "
        f"{stats['status']} status | {stats['zerados']} zerados (não encontrados)"
    )
    return updates


# ─── PASSO 4: Envia atualizações em lote para WooCommerce ───────────────────
def send_updates_to_woocommerce(updates: list) -> tuple[int, int]:
    total = len(updates)
    ok, failed = 0, 0
    sent_prices = {u["id"]: u["regular_price"] for u in updates if "regular_price" in u}

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
                    circuit.success()
                    # FIX: o batch retorna 200 mesmo com erro por item → checa um a um
                    batch_ok, batch_fail = 0, 0
                    for item in response.json().get("update", []):
                        pid = item.get("id")
                        if "error" in item:
                            batch_fail += 1
                            err = item["error"]
                            print(f"    ❌ ID {pid}: {err.get('code')} — {err.get('message')}")
                            continue
                        # Confere se o preço realmente gravou
                        if pid in sent_prices and item.get("regular_price") != sent_prices[pid]:
                            batch_fail += 1
                            print(
                                f"    ⚠️  ID {pid} (SKU {item.get('sku')}): preço enviado {sent_prices[pid]}, "
                                f"WC gravou '{item.get('regular_price')}'"
                            )
                            continue
                        batch_ok += 1
                    ok += batch_ok
                    failed += batch_fail
                    print(f"    ✅ Lote {batch_num}: {batch_ok} ok, {batch_fail} com problema.")
                    break
                else:
                    wait = BACKOFF_BASE ** attempt
                    print(f"    ❌ Erro {response.status_code} no lote {batch_num}. Tentativa {attempt}/{MAX_RETRIES}. Aguardando {wait}s...")
                    print(f"       {response.text[:300]}")
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
                failed += len(batch)
                break

        time.sleep(1)

    return ok, failed


# ─── Execução Principal ───────────────────────────────────────────────────────
def main():
    print(f"\n🚀 Iniciando sincronização EUTEC ↔ Agis{' (DRY RUN — nada será gravado)' if DRY_RUN else ''}\n")
    print(f"💲 Regra de preço: Agis / {MARGIN_DIVISOR} | +R${PRICE_ADDON_VALUE:.2f} se final <= R${PRICE_ADDON_LIMIT:.2f}\n")

    df_wc = get_eutec_products()
    if df_wc.empty:
        print("⚠️  Nenhum produto com gerenciamento de estoque na EUTEC. Encerrando.")
        return

    skus_eutec = df_wc["sku"].dropna().unique().tolist()
    agis_data = get_agis_data_for_skus(skus_eutec)

    # Trava de sanidade: Agis em manutenção devolvendo items[] vazio
    ratio = len(agis_data) / max(len(skus_eutec), 1)
    if ratio < MIN_FOUND_RATIO:
        print(
            f"\n🔴 TRAVA DE SANIDADE: Agis devolveu só {len(agis_data)} de {len(skus_eutec)} SKUs "
            f"({ratio:.1%} < {MIN_FOUND_RATIO:.0%}).\n"
            f"   Provável manutenção de catálogo. Abortando SEM zerar estoque."
        )
        sys.exit(3)

    updates = build_update_payload(df_wc, agis_data)

    if not updates:
        print("\n✅ Nenhuma atualização necessária. Tudo sincronizado!")
        return

    if DRY_RUN:
        print(f"\n🧪 DRY RUN: {len(updates)} atualizações seriam enviadas. Nada foi gravado.")
        return

    ok, failed = send_updates_to_woocommerce(updates)

    print("\n✅ Sincronização concluída!")
    print(f"   Produtos EUTEC:          {len(df_wc)}")
    print(f"   Encontrados na Agis:     {len(agis_data)}")
    print(f"   Atualizações enviadas:   {len(updates)}")
    print(f"   Gravadas com sucesso:    {ok}")
    print(f"   Com problema:            {failed}")

    if failed:
        sys.exit(4)  # marca o job do GitHub Actions como falho para você ver


if __name__ == "__main__":
    main()
