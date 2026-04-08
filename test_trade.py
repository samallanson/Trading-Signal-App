import os
from dotenv import load_dotenv
import oandapyV20
import oandapyV20.endpoints.orders as orders
from oandapyV20 import API as OandaAPI

load_dotenv()

oanda = OandaAPI(access_token=os.getenv("OANDA_API_KEY"), environment=os.getenv("OANDA_ENVIRONMENT", "practice"))

order_data = {
    "order": {
        "type": "MARKET",
        "instrument": "XAU_USD",
        "units": "1",
        "timeInForce": "FOK",
        "positionFill": "DEFAULT"
    }
}

r = orders.OrderCreate(os.getenv("OANDA_ACCOUNT_ID"), data=order_data)
oanda.request(r)
print("Test trade placed successfully!")