-- Chat room append, atomic per room. No mailbox, Source or Work authority.
-- KEYS[1]=seq KEYS[2]=log KEYS[3]=ai_streak
-- ARGV[1]=speaker kind ('human'|'ai'|'system'; system leaves the AI streak as is) ARGV[2]=AI streak limit
-- ARGV[3]=max messages kept ARGV[4]=TTL seconds
-- ARGV[5]=non-empty JSON object without seq, produced by chat_room.py
local kind = ARGV[1]
local limit = tonumber(ARGV[2])
local maxlen = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local streak = tonumber(redis.call('GET', KEYS[3]) or '0')
if kind == 'ai' and streak >= limit then
  return cjson.encode({error = 'ai_streak_limit', streak = streak, limit = limit})
end
local seq = redis.call('INCR', KEYS[1])
-- Splice seq into the caller's JSON object as-is (no cjson re-encode, which
-- would turn an empty mentions list into {} and re-escape text).
local encoded = '{"seq":' .. seq .. ',' .. string.sub(ARGV[5], 2)
redis.call('RPUSH', KEYS[2], encoded)
redis.call('LTRIM', KEYS[2], -maxlen, -1)
if kind == 'ai' then
  streak = redis.call('INCR', KEYS[3])
elseif kind == 'human' then
  redis.call('SET', KEYS[3], '0')
  streak = 0
end
for i = 1, 3 do
  redis.call('EXPIRE', KEYS[i], ttl)
end
return cjson.encode({message = encoded, ai_streak = streak})
