"""Contains methods for accessing the API Endpoints"""

import types

from . import (
    get_atm_option_contracts_for_expiries,
    get_candles,
    get_daily_expiry_breakdown,
    get_greek_exposure,
    get_greek_exposure_by_expiry,
    get_greek_exposure_by_strike,
    get_greek_exposure_by_strike_expiry,
    get_greeks_by_strike_expiry,
    get_info,
    get_insider_trades,
    get_max_pain,
    get_net_premium_ticks,
    get_open_interest_change,
    get_option_chains,
    get_option_contracts,
    get_option_volume_by_price_level,
    get_options_volume,
    get_risk_reversal_skew,
    get_sector_tickers,
    get_spot_exposures,
    get_spot_exposures_by_strike,
    get_volatility_term_structure,
    get_volume_by_price_level,
    get_volume_open_interest_by_expiry,
)


class StockEndpoints:
    @classmethod
    def get_sector_tickers(cls) -> types.ModuleType:
        """
        Return Tickers for a Given Sector
        =======================================================================

        Returns a list of tickers which are in the given sector.

        """
        return get_sector_tickers

    @classmethod
    def get_atm_option_contracts_for_expiries(cls) -> types.ModuleType:
        """
        ATM option contracts for the given expiries
        =======================================================================

        Returns the ATM option contracts for the given expirations

        """
        return get_atm_option_contracts_for_expiries

    @classmethod
    def get_daily_expiry_breakdown(cls) -> types.ModuleType:
        """
        Option Order Flow Grouped By Expiry on a Given Date for a Given Ticker
        =======================================================================

        Returns all expirations for the given Trading Day for a ticker.

        """
        return get_daily_expiry_breakdown

    @classmethod
    def get_greek_exposure(cls) -> types.ModuleType:
        """
                Greek Exposure
                =======================================================================

                Greek Exposure is the assumed greek exposure that market makers are exposed to. The most popular greek exposure is gamma exposure (GEX).

        Investors and large funds lower risk and protect their money by selling calls and buying puts. Market makers provide the liquidity to facilitate these trades. GEX assumes that market makers are part of every transaction and that the bulk of their transactions are buying calls and selling puts to investors hedging their portfolios. If a market maker has one contract open with a gamma value of 0.05, then that market maker is exposed to 0.05 * [100 shares] of gamma. The total market maker exposure is calculated by summing up the exposure values of all open contracts determined by the daily open interest. Market makers profit from the bid-ask spreads and as such, they constantly gamma hedge (they buy and sell shares to keep their positions delta neutral).

        Long call positions are positive gamma - as the stock price increases and delta rises (approaches 1), market makers hedge by selling shares, and they buy shares if the stock price decreases and delta falls.

        Short put positions are negative gamma - as the stock price increases and delta falls (approaches -1), market makers hedge by buying shares, and they sell shares if the stock price decreases and delta rises.

        As such, in the event of large positive gamma, volatility is suppressed as market makers will hedge by buying as the stock price decreases and selling as the stock price increases. And in the event of large negative gamma, volatility is amplified as market makers will hedge by buying as the stock price increases and selling as the stock price decreases.

        """
        return get_greek_exposure

    @classmethod
    def get_greek_exposure_by_expiry(cls) -> types.ModuleType:
        """
        Greek Exposure Grouped By Expiry All Strikes
        =======================================================================

        The greek exposure of a ticker grouped by expiry dates across all contracts on a given market date.

        """
        return get_greek_exposure_by_expiry

    @classmethod
    def get_greek_exposure_by_strike(cls) -> types.ModuleType:
        """
        Greek Exposure Grouped By Strike All Expiries On A Given Date
        =======================================================================

        The greek exposure of a ticker grouped by strike price across all contracts on a given market date.

        """
        return get_greek_exposure_by_strike

    @classmethod
    def get_greek_exposure_by_strike_expiry(cls) -> types.ModuleType:
        """
        Greek Exposure By Strike And Expiry
        =======================================================================

        The greek exposure of a ticker grouped by strike price for a specific expiry date.

        """
        return get_greek_exposure_by_strike_expiry

    @classmethod
    def get_greeks_by_strike_expiry(cls) -> types.ModuleType:
        """
        Option Greeks by Expiry All Strikes
        =======================================================================

        Returns the greeks for each strike for a single expiry date.

        """
        return get_greeks_by_strike_expiry

    @classmethod
    def get_risk_reversal_skew(cls) -> types.ModuleType:
        """
        Historical Risk Reversal Skew by Expiry and Ticker
        =======================================================================

        Returns the historical risk reversal skew (the difference between put and call volatility) at a delta of 0.25 or 0.1 for a given expiry date.

        """
        return get_risk_reversal_skew

    @classmethod
    def get_info(cls) -> types.ModuleType:
        """
        Ticker Information
        =======================================================================

        Returns a information about the given ticker.

        """
        return get_info

    @classmethod
    def get_insider_trades(cls) -> types.ModuleType:
        """
                Insider buy & sells
                =======================================================================

                Returns the total amount of purchases & sells as well as notional values for insider transactions
        for the given ticker

        """
        return get_insider_trades

    @classmethod
    def get_max_pain(cls) -> types.ModuleType:
        """
        Max Pain
        =======================================================================

        Returns the max pain for all expirations for the given ticker for the last 120 days

        """
        return get_max_pain

    @classmethod
    def get_net_premium_ticks(cls) -> types.ModuleType:
        """
                Net Prem Ticks
                =======================================================================

                Returns the net premium ticks for a given ticker which can be used to build the following chart:
        ![Net Prem chart](https://i.imgur.com/Rom1kcB.png)

        ----
        Each tick is resembling the data for a single minute tick. To build a daily chart
        you would have to add the previous data to the current tick:
        ```javascript
        const url =
          'https://api.unusualwhales.com/api/stock/AAPL/net-prem-ticks';
        const options = {
          method: 'GET',
          headers: {
            Accept: 'application/json',
            Authorization: 'Bearer YOUR_TOKEN'
          }
        };

        fetch(url, options)
        .then(r => r.json())
        .then(r => {
          const {data} = r.data;
          const fieldsToSum = [
            \"net_call_premium\",
            \"net_call_volume\",
            \"net_put_premium\",
            \"net_put_volume\"
          ];

          let result = [];
          data.forEach((e, idx) => {
            e.net_call_premium = parseFloat(e.net_call_premium);
            e.net_put_premium = parseFloat(e.net_put_premium);
            if (idx !== 0) {
              fieldsToSum.forEach((field) => {
                e[field] = e[field] + result[idx-1][field];
              })
            }
            result.push(e);
          })

          return result;
        });

        ```

        """
        return get_net_premium_ticks

    @classmethod
    def get_candles(cls) -> types.ModuleType:
        """
                OHLC
                =======================================================================

                Returns the Open High Low Close (OHLC) candle data for a given ticker.

        Results are limitted to 2,500 elements even if there are more available.

        """
        return get_candles

    @classmethod
    def get_open_interest_change(cls) -> types.ModuleType:
        """
                OI Change
                =======================================================================

                Returns the tickers contracts' OI change data ordered by absolute OI change (default: descending).
        Date must be the current or a past date. If no date is given, returns data for the current/last market day.

        """
        return get_open_interest_change

    @classmethod
    def get_option_chains(cls) -> types.ModuleType:
        r"""
                Tradeable Option Contracts By Ticker
                =======================================================================

                Returns all option symbols for the given ticker that were present at the given day.

        If no date is given, returns data for the current/last market day.

        You can use the following regex to extract underlying ticker, option type, expiry & strike:
        `^(?<symbol>[\w]*)(?<expiry>(\d{2})(\d{2})(\d{2}))(?<type>[PC])(?<strike>\d{8})$`

        Keep in mind that the strike needs to be divided by 1,000.

        """
        return get_option_chains

    @classmethod
    def get_option_contracts(cls) -> types.ModuleType:
        """
        Option contracts
        =======================================================================

        Returns all option contracts for the given ticker

        """
        return get_option_contracts

    @classmethod
    def get_option_volume_by_price_level(cls) -> types.ModuleType:
        """
        Option Price Levels
        =======================================================================

        Returns the call and put volume per price level for the given ticker.

        """
        return get_option_volume_by_price_level

    @classmethod
    def get_volume_open_interest_by_expiry(cls) -> types.ModuleType:
        """
        Volume & OI per Expiry
        =======================================================================

        Returns the total volume and open interest per expiry for the given ticker.

        """
        return get_volume_open_interest_by_expiry

    @classmethod
    def get_options_volume(cls) -> types.ModuleType:
        """
                Options Volume
                =======================================================================

                Returns the options volume & premium for all trade executions
        that happened on a given trading date for the given ticker.

        """
        return get_options_volume

    @classmethod
    def get_spot_exposures(cls) -> types.ModuleType:
        """
                Spot GEX exposures per 1min
                =======================================================================

                Returns the spot GEX exposures for the given ticker per minute.

        Spot GEX is the assumed $ value of the given greek (ie. gamma) exposure that market makers need to hedge per 1% change of the underlying stock's price movement. A positive value is long and a negative value is short.

        Investors and large funds lower risk and protect their money by selling calls and buying puts. Market makers provide the liquidity to facilitate these trades.

        GEX assumes that market makers are part of every transaction and that the bulk of their transactions are buying calls and selling puts to investors hedging their portfolios.

        If a market maker has one contract open with a gamma value of 0.05, then if the underlying stock price moves by 1%, that market maker is exposed to $[0.05 * 100 shares * 0.01 * stock price * underlying parameter of the greek variable (for gamma this variable is the stock price)]. The total market maker spot exposure is calculated by summing up the spot exposure of all open contracts determined by the daily open interest or by volume.

        Market makers profit from the bid-ask spreads and as such, they constantly gamma hedge (they buy and sell shares to keep their positions delta neutral).

        Long call positions are positive gamma - as the stock price increases and delta rises (approaches 1), market makers hedge by selling shares, and they buy shares if the stock price decreases and delta falls.

        Short put positions are negative gamma - as the stock price increases and delta falls (approaches -1), market makers hedge by buying shares, and they sell shares if the stock price decreases and delta rises.

        As such, in the event of large positive gamma, volatility is suppressed as market makers will hedge by buying as the stock price decreases and selling as the stock price increases. And in the event of large negative gamma, volatility is amplified as market makers will hedge by buying as the stock price increases and selling as the stock price decreases.

        """
        return get_spot_exposures

    @classmethod
    def get_spot_exposures_by_strike(cls) -> types.ModuleType:
        """
                Spot GEX exposures by strike
                =======================================================================

                Returns the most recent spot GEX exposures across all strikes for the given ticker on a given date. Calculated either with open interest or with volume.

        Spot GEX is the assumed $ value of the given greek (ie. gamma) exposure that market makers need to hedge per 1% change of the underlying stock's price movement. A positive value is long and a negative value is short.

        Investors and large funds lower risk and protect their money by selling calls and buying puts. Market makers provide the liquidity to facilitate these trades.

        GEX assumes that market makers are part of every transaction and that the bulk of their transactions are buying calls and selling puts to investors hedging their portfolios.

        If a market maker has one contract open with a gamma value of 0.05, then if the underlying stock price moves by 1%, that market maker is exposed to $[0.05 * 100 shares * 0.01 * stock price * underlying parameter of the greek variable (for gamma this variable is the stock price)]. The total market maker spot exposure is calculated by summing up the spot exposure of all open contracts determined by the daily open interest or by volume.

        Market makers profit from the bid-ask spreads and as such, they constantly gamma hedge (they buy and sell shares to keep their positions delta neutral).

        Long call positions are positive gamma - as the stock price increases and delta rises (approaches 1), market makers hedge by selling shares, and they buy shares if the stock price decreases and delta falls.

        Short put positions are negative gamma - as the stock price increases and delta falls (approaches -1), market makers hedge by buying shares, and they sell shares if the stock price decreases and delta rises.

        As such, in the event of large positive gamma, volatility is suppressed as market makers will hedge by buying as the stock price decreases and selling as the stock price increases. And in the event of large negative gamma, volatility is amplified as market makers will hedge by buying as the stock price increases and selling as the stock price decreases.

        """
        return get_spot_exposures_by_strike

    @classmethod
    def get_volume_by_price_level(cls) -> types.ModuleType:
        """
                Off/Lit Price Levels
                =======================================================================

                Returns the lit & off lit stock volume per price level for the given ticker.

        The price level is calculated by dividing the stock volume by the total
        ----
        Important: The volume does **NOT** represent the full market dialy volume. It
        only represents the volume of executed trades on exchanges operated by Nasdaq
        and FINRA off lit exchanges.

        """
        return get_volume_by_price_level

    @classmethod
    def get_volatility_term_structure(cls) -> types.ModuleType:
        """
        Implied Volatility Term Structure
        =======================================================================

        The average of the latest volatilities for the at the money call and put contracts for every expiry date.

        """
        return get_volatility_term_structure
