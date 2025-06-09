import datetime
import math
from typing import Dict, Any, List
from json import load
import sys
import asyncio
import logging



logger = logging.getLogger("IbkrMonitor")


# Local libraries. We need to retrieve the project path from the config file before importing
def read_config() -> Dict[str, str]:
    with open("config.json", 'r') as file:
        config_data = load(file)   
    return config_data

config_data = read_config()
sys.path.append(config_data["project_path"])

from src.interfaces.google_sheets_interface import GoogleSheetsInterface, connectToGoogleSheets
from src.brokers.interactive_brokers import InteractiveBrokers
from src.data_providers.data_manager import DataManager
from src.core.custom_types import BrokerConfig, Instrument, Position, Portfolio
from src.interfaces.telegram import send_to_telegram


CONTROL_ESTRATEGIAS_SHEET = "1OlZV7I92GORFKWB3kUe3UK3rf3FGuyfnqj6oCLorX2Y"
CREDENTIALS = "credentials_Google_NF.json"
VERBOSE  = True

# Diccionario persistente en memoria
class PortfolioTracker():
    """
    A class to track and manage a portfolio of financial instruments specific for IBKR.
    It creates a list of Ticker objects for each instrument in the portfolio, including
    underlyings if the instrument is an option. The ticker objects in the list are updated
    automatically by the TWS.
    
    Attributes:
    -----------
    tickerDictionary : Dict[str, Any]
        A dictionary to store ticker information for each symbol.
    portfolioPrices : Dict[str, Any]
        A dictionary to store the mark prices for each symbol in the portfolio.
    Methods:
    --------
    __init__():
        Initializes the PortfolioTracker with empty dictionaries for tickers and prices.
    updateMarkPrices() -> None:
        Updates the mark prices for each symbol in the portfolio if the mark price is valid.
    convertDictToList() -> List[List[Any]]:
        Converts the portfolio prices dictionary to a list of lists for easier manipulation and display.
    create(ibkr: InteractiveBrokers) -> List[List[Any]]:
        Initializes the portfolio with positions fetched from the Interactive Brokers client and updates mark prices.
    update() -> List[List[Any]]:
        Updates the mark prices and returns the updated portfolio prices as a list of lists.
    """
    
    
    def __init__(self):
        self.tickerDictionary: Dict[str, Any] = {}
        self.instrumentDictionary: Dict[str, Instrument] = {}
        self.portfolioPrices:  Dict[str, Dict[str, Any]] = {}
        self.controlEstrategias = connectToGoogleSheets(retries = 3, delay = 1, sheetToTest = "Maestro", credentials = CREDENTIALS, workingDocument = CONTROL_ESTRATEGIAS_SHEET, tokenPath = "token.pickle")
        self.lastPortfolioTime = datetime.datetime.min
        
    def connectGoogleSheets(self, retries: int = 3) -> GoogleSheetsInterface:
        logger.info(f"Trying to connect to Google Sheets. {retries} retries left")
        for i in range(retries):
            try:
                result = GoogleSheetsInterface(CREDENTIALS, CONTROL_ESTRATEGIAS_SHEET, "token.pickle")
                if result.connected:
                    logger.info("Succesfully connected to Google Sheets")
                    return result
                else:
                    logger.warning(f"Failed to connect to Google Sheets. {retries - i -1} retries left")
            except Exception as e:
                logger.error(f"Method connectGoogleSheets. Error connecting to Google Sheets: {e}")
        raise Exception("Error connecting to Google Sheets")
    
    
    def updateMarkPrices(self, rtData: DataManager, close: bool = False) -> None:                    
        try:           
            for _ , instrument in self.instrumentDictionary.items():
                marketData = rtData.getBestMarketData(instrument)                              
                if not marketData:
                    price = 0
                    priceType = "N/A"
                    closePrice = 0
                elif instrument.instrumentType == "OPT":
                    price = marketData.markPrice 
                    priceType = "Mark" 
                    closePrice = marketData.closePrice
                else:
                    price = marketData.last                     
                    priceType = "Last"
                    closePrice = marketData.closePrice
                price = 0 if math.isnan(price) else price
                closePrice = 0 if math.isnan(closePrice) else closePrice 
                timeStr = marketData.timestamp.strftime("%Y-%m-%d %H:%M:%S") if marketData else "N/A"                
                self.portfolioPrices[instrument.symbol] = {
                        "markPrice": price,
                        "priceType": priceType,
                        "time": timeStr,
                        "Close": closePrice
                }
        except Exception as e:
            logger.error(f"Method updateMarkprices. Error updating mark prices: {e}")         

  

    def convertDictToList(self) -> List[List[Any]]:
        try:
            data_list = [["Symbol", "Mark Price", "Price Type", "Time", "Close (Mark)"]]
            for symbol, data in self.portfolioPrices.items():
                data_list.append([symbol,data["markPrice"], data["priceType"], data["time"], data["Close"]])
            return data_list
        except Exception as e:
            logger.error(f"Method convertDictToList. Error converting dictionary to list: {e}")
            return []
        

    def addToInstrumentDictionary(self, listOfInstruments: list[Instrument]) -> None:
        for instrument in listOfInstruments:
            self.instrumentDictionary[instrument.symbol] = instrument       



    def create(self, ibkr: InteractiveBrokers, rtData: DataManager, verbose: bool = VERBOSE) -> None:
        """
            Fetchs open positions and their underlyings at IBKR, and creates a list of tickers
            containing real time data (mark prices) for each position. It initially sets
            to 0 all mark prices
        Args:
            ibkr (InteractiveBrokers): An instance of the InteractiveBrokers class.
        Returns: None            
        """
        try:
            if ibkr.RequestClient is None:
                logger.error("Error accessing broker")
                return 
            positions = ibkr.RequestClient.fetchPositions()   
            
            if not positions:
                logger.info(">>No positions found in the portfolio. Creating empty portfolio tracker.")
                return

            if verbose:
                logger.info("Fetching positions to create  portfolio tracker")
                
            for position in positions.positions.values():
                rtData.addInstrument(position.instrument)
                self.instrumentDictionary[position.symbol] = position.instrument
                self.tickerDictionary[position.symbol] = rtData.providers["IBKR"].getTicker(position.symbol)
                self.portfolioPrices[position.symbol] = {"markPrice": 0, "priceType": "Mark", "time": "", "Close": 0}
            underlyingSymbols = self.getUnderlyings(list(positions.positions.values()))
            for underlyingSymbol in underlyingSymbols:                
                self.tickerDictionary[underlyingSymbol] = rtData.providers["IBKR"].getTicker(underlyingSymbol)
                self.portfolioPrices[underlyingSymbol] = {"markPrice": 0, "priceType": "Mark", "time": "", "Close": 0}
                self.instrumentDictionary[underlyingSymbol] = Instrument(symbol= underlyingSymbol, exchange= "SMART")                
            logger.info ("Portfolio tracker created. Waiting for tickers to load...")
            ibkr.RequestClient.sleepIBKR(7)    
        except asyncio.TimeoutError as e:          
            msg = f"Method PortfolioTracker.create. Timeout error: {e}"
            logger.error(msg)

    @staticmethod
    def getUnderlyings(positions: List[Position]) -> List[str]:
        underlyingsSet: set[str] = set()
        for position in positions:
            if position.instrument.underlyingSymbol != "":
                underlyingsSet.add(position.instrument.underlyingSymbol)
        return list(underlyingsSet) 
    
    
    def refreshTickerDictionary(self, ibkr: InteractiveBrokers, rtData: DataManager, instrumentList: list[Instrument]) -> None:
        """
        Updates the ticker dictionary by adding new items found in the IBKR portfolio
        and removing items that are no longer in the IBKR portfolio.
        
        Args:
            ibkr (InteractiveBrokers): An instance of the InteractiveBrokers class.
        """
        if ibkr.RequestClient is None:
            logger.error("Error accessing broker")
            return
        
        if not self.tickerDictionary:
            self.create(ibkr, rtData, True)
            return
        
        portfolio: Portfolio = ibkr.RequestClient.fetchPositions()
        if not portfolio:
            logger.info("No positions found in the portfolio. Creating empty portfolio tracker.")
            return
        
        currentSymbols = list(portfolio.positions.keys())
        currentPositions = list(portfolio.positions.values())
        underlyingSymbols = self.getUnderlyings(currentPositions)
        currentSymbols.extend(underlyingSymbols)
        for instrument in instrumentList:
            currentSymbols.append(instrument.symbol)

        # Add new items
        for symbol in currentSymbols:
            if symbol not in self.tickerDictionary:
                rtData.addInstrument(Instrument(symbol=symbol, exchange="SMART"))            
                self.tickerDictionary[symbol] = rtData.providers["IBKR"].getTicker(symbol)
                self.portfolioPrices[symbol] = {"markPrice": 0, "priceType": "Mark", "time": "", "Close": 0}
            
        
        # Remove items that are no longer in the portfolio
        '''
        symbolsToRemove = set(self.tickerDictionary.keys()) - set(currentSymbols)
        for symbol in symbolsToRemove:
            del self.tickerDictionary[symbol]
            del self.portfolioPrices[symbol]
        '''


    def update(self, rtData: DataManager, close: bool = False) -> List[List[Any]]:
        """
            Updates the portfolio data by fetching the latest market prices and converting the data to a list format.
            This method performs the following steps:
            Args:
                close (bool): A boolean flag to indicate if the close price should be updated.
            Returns:
                List[List[Any]]: A list of lists containing the updated portfolio data.
        """

        self.updateMarkPrices(rtData,close)
        data_list = self.convertDictToList()
        return data_list
    

    def writeToGoogleSheets(self, dataList: List[List[Any]]) -> None:
        """
            Writes the portfolio data to a Google Sheet.
            Args:
                dataList (List[List[Any]]): A list of lists containing the portfolio data.
        """
        self.controlEstrategias.writeDataToSheet(sheet_name= "Maestro", data= dataList, start_column= "G", start_row  = 1)

def test() -> None:
    portfolioTracker = PortfolioTracker()

if __name__ == "__main__":
    test()

