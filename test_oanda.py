import os
from dotenv import load_dotenv
import oandapyV20
import oandapyV20.endpoints.accounts as accounts
from oandapyV20 import API as OandaAPI

load_dotenv()

oanda = OandaAPI(access_token=os.getenv("OANDA_API_KEY"), environment=os.getenv("OANDA_ENVIRONMENT", "practice"))
r = accounts.AccountSummary(os.getenv("OANDA_ACCOUNT_ID"))
oanda.request(r)
print("Connected! Balance: " + r.response["account"]["balance"] + " " + r.response["account"]["currency"])