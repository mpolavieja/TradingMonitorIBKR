''' 
                  **  Main module for Interactive Brokers monitor **

  IMPORTANT: In order to receive manual orders, the client_id must be set to 0 
  Setting Master client_Id to any number different than zero will redirect events from
  other API clients but NOT manual orders!!
  see https://interactivebrokers.github.io/tws-api/order_submission.html#order_status 

  newOrderEvent is NOT triggered when a manual order is submitted. Use onOrderStatus instead

'''
# For local libraries we need to retrieve the project path from the config file before importing
import json, sys
with open("config.json", 'r') as file:
    configData = json.load(file) 
sys.path.append(configData["project_path"])
from src.system.dual_logging import LazyLogger

logger = LazyLogger.getLogger("IbkrMonitor", "./logs")
logger.info("Starting Trading Monitor...")

# Standard libraries
import datetime
from math import isnan 
import threading
import asyncio
import os
import signal
from typing import Dict

# Local libraries
from gui import ShortAvailabilityChecker, tk
from ib_async import util, CommissionReport
from src.brokers.interactive_brokers import Stock, InteractiveBrokers, IbkrTrade, IbkrFill, Ticker
import src.interfaces.telegram as telegram
from src.interfaces.email_lib import sendFromGmail
from src.core.custom_types import BrokerConfig, Position, Portfolio, Instrument
from src.data_providers.data_manager import DataManager
from src.data_providers.ibkr_dataprovider import IbkrDataProvider

from portfolio_monitor import PortfolioTracker


shortableSharesDict: Dict[str, float]  = {}
# Global variables
RECONNECT_SECONDS: int = 50
CALLBACK_SECONDS: int = 20
callbackCounter: int = 0
nanCounterDict: Dict[str, int] = {}
broker: InteractiveBrokers
portfolioTracker: PortfolioTracker


def onDisconnected():
    global broker
    broker.connected = False
    if broker.tryToReconnect:
        reConnect(broker)
 

def reConnect(brokerClient:InteractiveBrokers)-> bool:
    try:  
        brokerClient.reconnecting = True
        disconnectionTime = datetime.datetime.now()
        waitSeconds = 5
        while not brokerClient.connected and brokerClient.tryToReconnect:               
            brokerClient.disconnect(tryToReconnect= True)
            # It is important to fill all the parameters when trying to reconnect!!
            # We first try a fast reconnection, if not successful, then we give enough timeout and 
            # wait seconds to avoid trying to reconnect when reconnecting is already succeeding
            util.sleep(waitSeconds) 
            brokerClient.IbkrRequest.connectSyncSimple(clientId= 0)          
            waitSeconds = RECONNECT_SECONDS
            util.sleep(waitSeconds)     
            if not brokerClient.connected:
                logger.error(f'>>Failed to connect to Interactive Brokers. Retrying in {waitSeconds} seconds...')
                continue                        
            disconnectionDuration = datetime.datetime.now()- disconnectionTime
            logger.info(f'Reconnected to Interactive Brokers after {int(disconnectionDuration.total_seconds())} seconds')
            brokerClient.reconnecting = False
            suscribeMarketData()
        return True
            
    except Exception as e:
        logger.info(f'Error: {e}')
        return False


def notifyCallBackStatus():
    global callbackCounter
    callbackCounter += 1
    current_time = datetime.datetime.now().time()
    if current_time.hour == 9 and current_time.minute == 50 and callbackCounter > 30:
        logger.info(f"Call back function running. It is {current_time}")
        callbackCounter = 0
  

# This is the best event to capture order events. NewOrderEvent does not seem to trigger on manual orders and does
# not seem to exist in IB native API.  onOpenOrder does not seem to inform correctly about the status of the order 
def onOrderStatus(trade: IbkrTrade):
    if trade.orderStatus.status != "Submitted":
       return
       
    instrumentType = "contracts" if trade.contract.secType in ["FUT", "OPT"] else "shares"
    msg = f"Sent order to {trade.order.action} {int(trade.order.totalQuantity)} {trade.contract.localSymbol} {instrumentType} at {trade.order.lmtPrice}"
    telegram.send_to_telegram(msg)
    logger.info(msg)


def buildTradeMessage(trade: IbkrTrade, commission: float):
    execution = trade.fills[-1].execution
    msg = f"{execution.side} {int(execution.shares)} shares on {trade.contract.localSymbol} at {execution.price}. Commission: {commission}"
    return msg


# fill.commissionReport.commission no parece funcionar bien.  Devuelve 0.0
def onExecDetails(trade: IbkrTrade, fill:IbkrFill):    
    msg = buildTradeMessage(trade, fill.commissionReport.commission)    
    telegram.send_to_telegram(msg)
    
    # email_lib.sendFromGmail(["m.garcia@newfrontier.es", "mpolavieja@gmail.com"], "Ejecución New Frontier", msg)
    logger.info(msg)  
    pass


# onComssionReportEvent seems the most complete fill event 
# as it provides both the trade data and the commission data
# Problem is it triggers again all recent fills when TWS is restarted
# so olds events need to be filtered 
def onCommission(trade: IbkrTrade, fill: IbkrFill , report:CommissionReport):
    msg = buildTradeMessage(trade, report.commission)    
    fillTime = fill.time
    timeTresholdMinutes = 2
    if datetime.datetime.now(datetime.timezone.utc) - fillTime > datetime.timedelta(minutes = timeTresholdMinutes):
      logger.info(f"Se ha recibido el siguiente evento de hace {timeTresholdMinutes} minutos o más. No se notificará por mail ni telegram:")
      logger.info (msg)
      return    
    
    # PENDIENTE EJECUCIONES PARCIALES ¿ENVIAMOS SOLO CUANDO SON COMPLETAS? Y SI NUNCA SE COMPLETAN? RETENEMOS LAS PARCIALES X TIEMPO?
    telegram.send_to_telegram(msg)
    sendFromGmail(["m.garcia@newfrontier.es", "mpolavieja@gmail.com"], "Ejecución New Frontier", msg)
    logger.info(msg)  
    pass    


def notifyShortableShares(tickerSet: set[Ticker])-> None:    
    global shortableSharesDict
    global nanCounterDict
    ticker: Ticker 

    try:         
        for ticker in tickerSet:            
            if ticker.contract is None:
                continue
            symbol = ticker.contract.symbol                                     
            shortableShares = ticker.shortableShares       
            #print(f"Debug. Market data type: {ticker.marketDataType}. Symbol: {symbol}. Shortable shares: {shortableShares}")
            if isnan(shortableShares): 
                nanCounterDict[symbol] = nanCounterDict.get(symbol, 0) + 1
            else:
                shortableShares = 0 if shortableShares > 2_147_483_000 else shortableShares

            if nanCounterDict[symbol] > 10:
                nanCounterDict[symbol] = 0
                shortableShares = 0           

            prevShortableShares = shortableSharesDict.get(symbol, 0)            
            if (prevShortableShares != 0 and shortableShares == 0) or \
               (prevShortableShares <= 0 and shortableShares > 0):
                msg = f"Hay {int(shortableShares):,} acciones para préstamo en {symbol}"
                telegram.send_to_telegram(msg)
                logger.info(msg)
            if not isnan(shortableShares):
                shortableSharesDict[symbol] = shortableShares
                
    except asyncio.CancelledError as e:
        print(f"[notifyShortableShares] {datetime.datetime.now()} - Asyncio error Error, probably related to ib.sleep(): {e}")
    except Exception as e:
        print(f"[notifyShortableShares] {datetime.datetime.now()} - Unexpected error: {e}")



def suscribeMarketData()-> list[str]:   
    global app
    global broker
  
    logger.info("Suscribing Market Data...")
    if app is None:
        logger.error("Not possible to suscribe Market Data. Error initializing GUI")
        return []

    return app.readSymbols()    
    

def initshortableSharesDict(app: ShortAvailabilityChecker)-> None:
    global shortableSharesDict

    symbols = app.readSymbols()
    for symbol in symbols:
        shortableSharesDict[symbol] = -1 


def trackPortfolio(rtData: DataManager, updateSeconds: int = 20, manualTickers: list[str] = [])-> None:
    global portfolioTracker
    global broker
    global dataManager
   
    currentTime = datetime.datetime.now()
    if (currentTime - portfolioTracker.lastPortfolioTime).total_seconds() < updateSeconds:
        return
    logger.info(">>refreshing tickers...")
    portfolioTracker.refreshTickerDictionary(broker, rtData, manualTickers)
    logger.info(">>Updating portfolio prices...")
    if currentTime.hour == 22 and currentTime.minute <= 2:
        dataList = portfolioTracker.update(rtData,close = True)
    else:
        dataList = portfolioTracker.update(rtData,close = False)

    logger.info(">>Updating Google Sheets...")
    portfolioTracker.writeToGoogleSheets(dataList)
    portfolioTracker.lastPortfolioTime = currentTime



# This is the schedulded callback funcion
# TODO: refresh symbols on the GUI 
def checkConnection(brokerClient: InteractiveBrokers, rtData: DataManager, manualTickers: list[str])-> bool:   
    
    global app 
    
    notifyCallBackStatus()   
    
    '''
    if not brokerClient.tryToReconnect:
        return False    
    
    if not brokerClient.connected and not brokerClient.reconnecting:
        brokerClient.connected = reConnect(brokerClient)    
        return False
    
    if brokerClient.RequestClient is None:
        brokerClient.connected = reConnect(brokerClient)
        return False
    '''
    trackPortfolio(rtData, manualTickers= manualTickers)

    nextRunTime = datetime.datetime.now() + datetime.timedelta(seconds=5)  
    brokerClient.IbkrRequest.tradingClient.schedule(nextRunTime, checkConnection, brokerClient, rtData, manualTickers) 
    
    if app is None:
        logger.error("Error checking connection. GUI (app) not initialized")
        return False
    
    if app.checkCallBack:
        app.checkCallBack = False
        logger.error( f"CallBack is executing correctly at {datetime.datetime.now()}")
    return True


def instrumentsToTrack(broker: InteractiveBrokers, manualTickers: list[str]) -> list[Instrument]:    
    
    if broker.RequestClient is None:
        return []
    
    instrumentDict: dict[str, Instrument] = {}
    currentPositions: list[Position] = []
    portfolio = broker.RequestClient.fetchPositions()    
   
    for position in portfolio.positions.values():
        currentPositions.append(position)
        instrumentDict[position.instrument.symbol] = position.instrument
    
    underlyingSymbols = PortfolioTracker.getUnderlyings(currentPositions)
    for symbol in underlyingSymbols:
        if symbol not in instrumentDict:
            instrumentDict[symbol] = Instrument(symbol=symbol, exchange="SMART")

    for symbol in manualTickers:
        if symbol not in instrumentDict:
            instrumentDict[symbol] = Instrument(symbol=symbol, exchange="SMART")

    return list(instrumentDict.values())


def main(): 
    global broker, portfolioTracker, app, ibkrData, dataManager

    print("")    
  
    logger.info("Starting IBKR Monitor....") 

    # The following line is mandatory in order to be able to automatically reconnect
    util.patchAsyncio()
    util.sleep(1)
    
    ibkrConfig = BrokerConfig(name="IBKR", port=7496, clientID=0, host="127.0.0.1")
    broker = InteractiveBrokers.initWithoutRiskManager(ibkrConfig)
    broker.IbkrRequest.connectSyncSimple()
    if broker.RequestClient is None:
        print("Error initializing Interactive Brokers")
        return
   
    broker.EventClient.eventClient.execDetailsEvent += onExecDetails            #type: ignore attribute not declared in broker abstract class
    broker.EventClient.eventClient.disconnectedEvent += onDisconnected          #type: ignore attribute not declared in broker abstract class
    broker.EventClient.eventClient.orderStatusEvent += onOrderStatus            #type: ignore attribute not declared in broker abstract class    
    broker.EventClient.eventClient.commissionReportEvent += onCommission        #type: ignore attribute not declared in broker abstract class 
    #broker.EventClient.eventClient.pendingTickersEvent += notifyShortableShares #type: ignore attribute not declared in broker abstract class   

    # Run the GUI in a separate thread
    logger.info ("Starting GUI....")
    
    gui_thread = threading.Thread(target=main_gui, daemon=True)
    gui_thread.start()

    broker.RequestClient.sleepIBKR(2)

    manualTickers= suscribeMarketData()    

    dataManager= DataManager()
    ibkrData = IbkrDataProvider(broker.RequestClient.tradingClient)
    dataManager.addDataProvider(ibkrData)
    portfolioTracker = PortfolioTracker()    
    instrumentList = instrumentsToTrack(broker, manualTickers)
    portfolioTracker.addToInstrumentDictionary(instrumentList)
    dataManager.start(instrumentList)

    nextRunTime = datetime.datetime.now() + datetime.timedelta(seconds=5)  
    broker.RequestClient.tradingClient.schedule(nextRunTime, checkConnection, broker, dataManager, manualTickers)
    broker.RequestClient.run()


def main_gui():   
    global app, broker
    
    def on_close():
        broker.tryToReconnect = False
        broker.disconnect()
        master.destroy() 
        # sys.exit() does not work, so we raise SIGINT to stop the main thread
        os.kill(os.getpid(), signal.SIGINT)
            
    master =  tk.Tk()
    app = ShortAvailabilityChecker(master)
    initshortableSharesDict(app)
    master.protocol("WM_DELETE_WINDOW", on_close)
    master.mainloop() 

                        

if __name__ == "__main__":    
    app = None
   

    main()


