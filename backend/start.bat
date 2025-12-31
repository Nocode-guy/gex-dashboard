@echo off
REM GEX Dashboard Start Script
REM Set your Tradier API key below

set TRADIER_API_KEY=uB6Q87tfYQwAQdnoUpCXqNRnKVCt

echo Starting GEX Dashboard...
python -m uvicorn app:app --reload --host 0.0.0.0 --port 8000
