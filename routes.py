"""Opt-in route installer; original service must explicitly inject verified Service.
No body/signature logging. No token or broad Redis authority accepted from request.
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from s2s import PATH,Refused
import json

def install_internal_route(app,*,service):
 if service is None:raise RuntimeError('EXPLICIT_SERVICE_REQUIRED')
 if any(getattr(r,'path',None)==PATH for r in app.routes):raise RuntimeError('ROUTE_ALREADY_PRESENT')
 @app.post(PATH)
 async def receive(request:Request):
  raw=bytearray()
  try:
   async for chunk in request.stream():
    raw.extend(chunk)
    if len(raw)>4096:return JSONResponse({'error':'REQUEST_TOO_LARGE'},status_code=413)
   # JSON parsers accepting duplicate keys create ambiguous signed semantics.
   def unique(pairs):
    d={}
    for k,v in pairs:
     if k in d:raise ValueError('duplicate')
     d[k]=v
    return d
   body=json.loads(raw,object_pairs_hook=unique)
   value=await run_in_threadpool(service.handle,body,request.headers.get('X-XJ-Signature',''))
   return JSONResponse(value)
  except Refused as e:return JSONResponse({'error':str(e)},status_code=409)
  except Exception:return JSONResponse({'error':'OUTCOME_UNKNOWN_QUERY_ONLY'},status_code=409)
