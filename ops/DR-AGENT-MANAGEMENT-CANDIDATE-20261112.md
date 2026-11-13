# DR Bot 原 Deer Agent 管理轉接候選

狀態：CANDIDATE_TESTED_NOT_DEPLOYED。這不是固定 Agent 管理正式上線回執。

## 實際改動

- `deer_agent_management.py`：可選的原 MCP 轉接器，僅提供 `agent_list`、`agent_read`、`agent_create`、`agent_receipt`。它不建模型客戶端、不使用 Grok API、不保存登入憑證、不建立另一個名冊或服務。
- `mcp_bridge.install(..., agent_management_resolver=None)`：原 MCP 的可信啟動注入點。預設不掛載；只有宿主提供 resolver 才沿原 `principal()` 與原 OAuth metadata 掛載四工具。沒有變更既有 OAuth scope。
- 使用原 Gateway `/api/agents` 與 `/api/agents/{name}`。原 API 的登入、CSRF、管理開關及 owner scope 全部由原 session transport 保持，不在本模組模擬或繞過。
- 建立預設 `tool_groups=[]`、`skills=[]`、`allowed_subagents=[]`，不以 null 隱式繼承全部工具或委派能力。明確請求的能力仍須原管理授權核准；設計卡不授權。
- 固定 Agent 修改：原 Store.update 是沒有版本條件的 upsert，本候選明確回 `ORIGINAL_AGENT_CAS_NOT_BOUND`，不以讀取雜湊後再 PUT 假裝原子 CAS。
- 固定 Agent 停止：原 DELETE 會刪除角色及共置記憶，不當停止使用。本候選回 `ORIGINAL_AGENT_STOP_NOT_BOUND`。此限制不否定另一工作線的 Codex 單項停止成果；兩者不是同一操作。

## 驗證

37 項 unittest 通過、exit 0：原 Deer 路由與真實臨時 SQLite 建立→獨立讀回→名冊、跨使用者隔離、原 API disabled 403、原名衝突409、缺可信管理綁定／防重拒絕、checkpoint-only拒絕、原 MCP owner不符拒絕、撤權／變更owner在效果前重核、失回不重送、輸入／路徑邊界、呼叫者跨await修改不影響授權內容、SDK tools/list與Context注入、錯誤回件不升格，以及不以PUT／DELETE冒修改／停止。

使用原備份後端的路由、SqlAgentStore、AgentRow與現有虛擬環境，測試只建立臨時SQLite。測試的帳號授權、session transport、once fence是**明示測具**；原有部分once fixture仍為記憶體測具；新增Journal測試使用原Source I/O及真實私有暫存目錄，驗重新開啟、同鍵衝突、競爭、保存失敗、STOP、symlink、root替換，以及與原路由/SQLite的整合。正式宿主持久防重尚未安裝。未啟動模型、正式Agent、Redis派工或新服務；不是具名獨立QA。完整 `make test` / `make test-blocking-io` 未執行，現有測試環境沒有 pytest。

## 啟用前必須補齊的原接線

1. 原 `agent_management_resolver(ctx)` 回傳 `OriginalAgentBinding`，資料來自可信啟動能力，不從tool JSON接收user_id、cookie、URL或角色grant。
2. resolver／`authorize` 每次核原MCP verified principal、Gateway具名user及live token_version、明確的管理授權、到期／撤回／STOP，以及這次要求的工具／委派範圍。`platform-checkpoint-only`紀錄不能被改名或升格為管理授權。`authority_scope`字串只是拒絕誤接的標記，不能自行建立授權。
3. `invoke(method,path,body)` 使用同一原帳號已受保護的Gateway session transport，固定原目的地、不跟隨redirect，保留CSRF／登入／agents_api.enabled。不得沿internal/test/default身分、合成帳號或另發PAT代替。
4. `invoke_once(action,key,digest,perform)` 必須沿**原持久operation fence與回執**：同鍵異內容拒絕；UNKNOWN只查原筆、不再呼叫perform；重啟不重播。原batch item的once fence不自動變成Agent名冊寫入的防重；缺原可用實作時保持NOT_BOUND，不建第二庫。
5. 先零派送驗身分與開關，再由原有授權對本管理範圍作具體啟用審查。只有真正接好後才更新工具目錄與共用指引，不提前宣稱全Agent可用。
6. 實際驗收：一個獨立無敏感測試角色，建→另一呼叫讀回同name/規格→查原名冊→原方式停用並讀回。若原停用或CAS尚無原子能力，分項標未完成，不用刪除記憶或停整服務補過。

## 現場與回退

本次原Codespace UI可開啟，但終端可見其他工作正在操作；沒有安裝本候選、沒有更改管理開關、session、policy、host pin、runtimeDB、D2或Gate1。磁碟低空間提醒已觀察，未prune或搬移資料。

新候選分支基於 `feat/deer-item-stop-20261112` 的 `e034b4d965b6d6f6f55c027ce6b16991438d2a72`。該版 `mcp_bridge.py` 與先前e8e1070c原檔相同；原所有停工差額保留。回退僅移除本次可選啟動參數與新模組；未啟用前無正式資料效果。未合併、不重啟、不部署。


## 本輪真實差額

原Codespace獨立Terminal 8唯讀核實：原agents.py SHA256 `64614da569c9a30297adf4de22511abb4263dbc2bf548e0118c3c25d695fbfc2`，與隔離測試來源相同；原config.yaml database.backend=sqlite，agents_api與agent_storage均未顯式配置（原類別預設管理API disabled、file名冊）。原Source目錄存在，當次有一筆未過期／未撤回的account-link，scope僅platform-checkpoint-only。這證明已有帳號綁定紀錄，不證管理授權或本Work持有該登入session。未讀取或匯出cookie、密碼、私鑰、token或完整user紀錄。

新增`deer_management_journal.py`沿同一原Source私有目錄保存create-only attempt/receipt，復用原private_directory/read_at/publish/encode的mode/owner/link/fsync檢查。constructor不建目錄或資料庫。attempt fsync成功後才能呼叫原效果，崩潰/失回/receipt保存失敗均保留UNKNOWN且不重播；`agent_receipt`獨立查同owner/grant/key與payloadDigest。這是程式與隔離實證，不是正式部署回執。

新增`deer_management_test_scope.py`只縮窄一個已存在的真管理binding：最長600秒、唯一角色`qa-dr-readback-20261112`、只list/read/create、三種capability list全空、名冊結果只返回該角色。它拒絕checkpoint-only binding，不能自行發管理grant。

enablement-review.json是PROPOSED_NOT_GRANTED的施工審閱卡，不是授權。正式管理issuer/原保護連線仍未接好，不開既有全域管理API、不冒用checkpoint link。不應把使用者的一般「開始」或本檔的scope文字當原系統已發管理grant。未變更目前外掛版本、原MCP alias、原服務部署或Gate1。
