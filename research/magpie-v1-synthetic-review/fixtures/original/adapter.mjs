// Offline-only BFF -> Magpie-shaped adapter. No network or credentials.
export function validateRequest(request) {
  if (!request || Object.getPrototypeOf(request) !== Object.prototype) return {ok:false,code:"INVALID_REQUEST"};
  const keys=Object.keys(request).sort().join(",");
  if (keys !== "model,protocol,requestId,tenantRef") return {ok:false,code:"INVALID_REQUEST"};
  if (!["chat","responses","messages"].includes(request.protocol)) return {ok:false,code:"INVALID_PROTOCOL"};
  for (const key of ["model","requestId","tenantRef"]) {
    if (typeof request[key] !== "string" || !/^[a-zA-Z0-9._-]{1,80}$/.test(request[key])) return {ok:false,code:"INVALID_REQUEST"};
  }
  return {ok:true};
}
export function executeMock({request, grant, transport}) {
  const valid=validateRequest(request);
  if (!valid.ok) return {status:"DENIED",code:valid.code};
  // This is only a synthetic grant contract, not trusted authentication.
  if (!grant || grant.verified !== true || grant.tenantRef!==request.tenantRef || grant.scope!=="model:invoke")
    return {status:"DENIED",code:"AUTH_DENIED"};
  if (typeof transport!=="function") return {status:"DENIED",code:"NO_TRANSPORT"};
  try {
    const response=transport({protocol:request.protocol,model:request.model,requestId:request.requestId});
    if (!response || response.requestId!==request.requestId) return {status:"UNKNOWN",code:"RECEIPT_MISMATCH"};
    if (response.status!==200) return {status:"DENIED",code:"UPSTREAM_ERROR",attempts:1};
    if (response.partial || response.usageVerified!==true) return {status:"UNKNOWN",code:"UNVERIFIED_USAGE",attempts:1};
    return {status:"MOCK_ACCEPTED",code:"MOCK_ONLY",attempts:1,usage:response.usage};
  } catch (_) {
    return {status:"UNKNOWN",code:"TRANSPORT_UNKNOWN",attempts:1};
  }
}
