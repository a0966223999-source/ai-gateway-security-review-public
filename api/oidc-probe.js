// Diagnostic only. Decoding a JWT does NOT verify its signature.
// Never return the token, claims, or user identifiers.
export default function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Robots-Tag', 'noindex, nofollow');
  if (req.method !== 'GET') return res.status(405).json({decision:'DENY',error:'METHOD_NOT_ALLOWED'});
  const token = req.headers['x-vercel-oidc-token'];
  let claims = null;
  if (typeof token === 'string' && token.split('.').length === 3) {
    try {
      const encoded = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      claims = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
    } catch { claims = null; }
  }
  const now = Math.floor(Date.now() / 1000);
  const aud = claims?.aud;
  const expectedIssuer = 'https://oidc.vercel.com/yoyo-5560';
  const expectedAudience = 'https://vercel.com/yoyo-5560';
  const expectedSubject = 'owner:yoyo-5560:project:ai-gateway-security-review-public:environment:preview';
  return res.status(200).json({
    decision: 'DENY',
    execution_enabled: false,
    token_present: typeof token === 'string',
    jwt_decodable: !!claims,
    issuer_matches: claims?.iss === expectedIssuer,
    audience_matches: typeof aud === 'string' ? aud === expectedAudience : Array.isArray(aud) && aud.includes(expectedAudience),
    subject_matches: claims?.sub === expectedSubject,
    time_window_matches: Number.isFinite(claims?.iat) && Number.isFinite(claims?.exp) && claims.iat <= now && claims.exp > now,
    claims_signature_verified: false,
    broker_authorized: false
  });
}
