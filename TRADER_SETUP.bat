@echo off
title AI Trader — Setup
echo ============================================
echo  AI Crypto Trader Setup (paper trading)
echo ============================================
echo.
echo You need a Claude API key from https://platform.claude.com
echo It is saved ONLY to .env on this computer.
echo.
pip install anthropic ccxt rich python-dotenv
echo.
set /p ANT_KEY=Paste your Claude API key (sk-ant-...):
set /p EXCH=Exchange to read prices from [kraken]:
if "%EXCH%"=="" set EXCH=kraken

:: Append (not overwrite) so UC Learn settings are kept
(
echo.
echo # AI crypto trader
echo ANTHROPIC_API_KEY=%ANT_KEY%
echo TRADER_MODE=paper
echo TRADER_EXCHANGE=%EXCH%
echo TRADER_SYMBOLS=BTC/USD,ETH/USD,SOL/USD
) >> .env

echo.
echo [OK] Saved. Paper trading starts with $10,000 of fake money.
echo      Next: double-click TRADE_ONCE.bat
echo.
pause
