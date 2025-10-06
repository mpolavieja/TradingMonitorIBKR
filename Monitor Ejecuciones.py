from ib_insync import *
import requests

def send_to_telegram(message):
    try:
        response = requests.post('https://api.telegram.org/bot{}/sendMessage'.format("6092797629:AAGEIBIYhrprAQJkeiOTs3YOuwzg0pK-Jqg"), 
                                json={
                                      'chat_id': -4063745280, 
                                      'text': message
                                     }
                                 )
        return response            
    except Exception as e:
        print("Error enviando mensaje por Telegram. Exception:", str(e))

# Create a callback function to handle price alerts
def price_update(Ticker):
    global alert 
    first_ticker = Ticker.pop()
    current_price = first_ticker.last
    if current_price is None:
        return()
    if (current_price <= target_price) and not alert:
        msj = f"{stock_symbol} ha perdido ${target_price}: {current_price}"
        print (msj)
        send_to_telegram (msj)
        alert = True
    else:    
        alert = current_price <= target_price
        

# Configuración y conexion a IB
ib_host = "127.0.0.1"
ib_port = 7496  # Puerto de conexión de TWS
ib_client_id = 0  # ID de cliente tiene que ir a 0 para detectar ejecuciones de ordenes manuales del TWS
ib = IB()
ib.connect(host=ib_host, port=ib_port, clientId=ib_client_id)

# Define stock alert
alert = False
stock_symbol = 'FFIE'
target_price = 1
stock = Stock(symbol=stock_symbol, exchange='SMART', currency='USD')
ticker = ib.reqMktData(stock,'', False, False)
ib.pendingTickersEvent += price_update


# Manage executions
response = send_to_telegram("Escuchando ejecuciones")
print(response)
def on_execution(trade,fill):
    if (trade.remaining() == 0):
        side = "Venta" if trade.order.action == "SELL" else "Compra"
        msj = f"{side} de {trade.order.totalQuantity} titulos en {trade.contract.symbol} @ {trade.order.lmtPrice}"
        print (msj)
        send_to_telegram (msj)

ib.execDetailsEvent += on_execution
# Ejecutar el script
print("Conectado a IB y Telegram. Esperando ejecuciones...")
ib.run()
