from trader import place_trade, get_balance

analysis = {
    "DIRECTION": "LONG",
    "INSTRUMENT": "XAUUSD",
    "ENTRY": "4730.90",
    "STOP_LOSS": "4725.00",
    "TAKE_PROFIT_1": "4735.00",
    "TAKE_PROFIT_2": "4740.00",
    "SETUP_QUALITY": "A+",
    "CONFIDENCE": "85",
    "BIAS": "BULLISH",
    "TRADE": "YES",
    "REASON": "Forced test trade"
}

print("Balance: " + str(get_balance()))
result = place_trade(analysis)
print("Trade placed: " + str(result))