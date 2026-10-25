import os
import math
from datetime import datetime, timezone
from urllib.parse import quote
import feedparser
import pandas as pd
import yfinance as yf
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

PASSWORD = os.getenv('TRADEIQ_PASSWORD', '')
SECRET = os.getenv('SESSION_SECRET', '')
if not PASSWORD or not SECRET:
    raise RuntimeError('Set TRADEIQ_PASSWORD and SESSION_SECRET environment variables before running.')
app = FastAPI(title='TradeIQ Free')
app.add_middleware(SessionMiddleware, secret_key=SECRET, https_only=os.getenv('COOKIE_HTTPS', '0') == '1', same_site='lax')
templates = Jinja2Templates(directory='templates')

def auth(req):
    return req.session.get('logged_in') is True

@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    if not auth(request):
        return templates.TemplateResponse('login.html', {'request':request})
    return templates.TemplateResponse('dashboard.html', {'request':request, 'data':None, 'symbol':'', 'error':None, 'news':[]})

@app.post('/login')
def login(request: Request, password: str = Form(...)):
    import hmac
    if hmac.compare_digest(password, PASSWORD):
        request.session['logged_in'] = True
        return RedirectResponse('/', status_code=303)
    return templates.TemplateResponse('login.html', {'request':request, 'error':'Incorrect password'}, status_code=401)

@app.post('/logout')
def logout(request: Request):
    request.session.clear()
    return RedirectResponse('/', status_code=303)

def numeric(v):
    return round(float(v), 2) if pd.notna(v) and math.isfinite(float(v)) else None

def research(symbol, mode):
    symbol = symbol.strip().upper()
    if not symbol or len(symbol)>24 or not all(c.isalnum() or c in '.-^' for c in symbol):
        raise ValueError('Enter a valid ticker such as RELIANCE.NS or TCS.NS')
    if '.' not in symbol and not symbol.startswith('^'):
        symbol += '.NS'
    interval, period = {'intraday':('5m','5d'), 'swing':('1d','6mo'), 'longterm':('1wk','2y')}[mode]
    frame = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    if frame.empty or len(frame)<35:
        raise ValueError('Insufficient price history for this ticker or interval. Free provider coverage varies.')
    close = frame['Close'].astype(float)
    high, low = frame['High'].astype(float), frame['Low'].astype(float)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    macd = ema12-ema26
    signal_line = macd.ewm(span=9, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100-100/(1+gain/loss.replace(0,float('nan')))
    tr = pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]
    last = float(close.iloc[-1]); e20=float(ema20.iloc[-1]); r=float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50
    bull = last>e20 and macd.iloc[-1]>signal_line.iloc[-1] and 45<=r<=70
    bear = last<e20 and macd.iloc[-1]<signal_line.iloc[-1] and 30<=r<=55
    assessment = 'BUY CANDIDATE' if bull else 'SELL CANDIDATE' if bear else 'WAIT'
    # Hypothetical levels are research references, not orders or execution instructions.
    stop = last-1.5*atr if bull else last+1.5*atr if bear else None
    target = last+3*atr if bull else last-3*atr if bear else None
    ts = frame.index[-1]
    return {'symbol':symbol,'mode':mode,'assessment':assessment,'price':numeric(last),'ema20':numeric(e20),
        'rsi':numeric(r),'macd':numeric(macd.iloc[-1]),'atr':numeric(atr),'stop':numeric(stop) if stop is not None else None,
        'target':numeric(target) if target is not None else None,'risk_reward':'2:1 (illustrative)' if stop is not None else 'N/A',
        'as_of':str(ts),'checked_at':datetime.now(timezone.utc).isoformat(),
        'explanation':('Price is above EMA20 with positive MACD and non-extreme RSI.' if bull else
            'Price is below EMA20 with negative MACD and weak RSI.' if bear else
            'Trend, momentum and RSI do not jointly confirm a setup.'),
        'provider':'Yahoo Finance (unofficial; potentially delayed)', 'candles':len(frame)}

@app.get('/analyze', response_class=HTMLResponse)
def analyze(request: Request, symbol: str = 'RELIANCE', mode: str = 'swing'):
    if not auth(request): return RedirectResponse('/', status_code=303)
    if mode not in ('intraday','swing','longterm'): mode='swing'
    data, error = None, None
    try: data = research(symbol,mode)
    except Exception as exc: error = str(exc)[:250]
    news=[]
    try:
        rss=feedparser.parse('https://news.google.com/rss/search?q='+quote(symbol+' NSE stock')+'&hl=en-IN&gl=IN&ceid=IN:en')
        news=[{'title':entry.get('title',''), 'link':entry.get('link','')} for entry in rss.entries[:8] if entry.get('link','').startswith('https://')]
    except Exception: pass
    return templates.TemplateResponse('dashboard.html',{'request':request,'data':data,'error':error,'symbol':symbol,'mode':mode,'news':news})
