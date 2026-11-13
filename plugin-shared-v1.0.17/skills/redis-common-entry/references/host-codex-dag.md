# 原宿主有界自動 DAG（v1.0.17）

同一原 X Redis Test、OAuth 帳號、Gateway／Holder／SQL／worker。使用者明確交辦一次後，宿主依前置完成自動推進，不需 Work 逐階段再送請求。普通保存仍 automatic_dispatch=false，不掃描其他待辦。

## 建立與查回

先呼叫 batch_host_status 核當次簽章心跳、parent/generation及execution_available。已授權保存時呼叫原 batch_task，items提供1–3個節點（key／prompt／驗收標準），goal是以下物件的精確JSON字串：

```json
{"schema":"deer-codex-dag-v1","goal":"原使用者大目標","dependencies":{"A":[],"B":[],"C":["A","B"]}}
```

dependencies的key必須與items的key完全相同。原Host在任何原Holder寫入前檢查完整節點集合、重複邊、缺前置、自依賴及循環；原Holder將goal、dag及items一次保存到原SQL execution_spec與原完整binding，不新增資料庫或schema。批次request仍受64KiB、goal字串12000字元及原prompt大小限制。

用新讀鍵 batch_status(include_plan=true,include_result=true,item_offset=0,item_limit=25)，核原 goal、dag.dependencies／order、所有原item IDs／prompt／標準一致，且pending/attempt=0，再明確執行。僅要求規劃／保存時不呼叫execute。

## 一次明確交辦

優先使用當次可見的 batch_execute_dag；它與原 batch_execute_batch 使用同一request schema。新工具尚未載入時，可直接用**既有 batch_execute_batch**，已在本Work真驗同一兼容入口，不需要未知欄位或另一HTTP client：

```json
{"parent_id":"<當次parent>","operation_key":"<新唯一執行鍵>","execution_mode":"codex_batch_once","batch_id":"<原批次>","item_ids":["<全部原節點ID>"],"timeout_seconds":90}
```

完整集合必須與已保存DAG一致；不接受只挑根節點、漏節點或外來item。宿主讀原不可變DAG，按拓樸順序依序執行。即使item_ids以C/B/A排列，也先A/B、再C。沿原writer/native once取得執行權，每項最多一次，最長90秒；整圖啟動預算300秒、最多3個節點，沿原Codex唯讀profile。不能以普通批次的100項上限宣稱宿主DAG支援100節點。

每個後繼送模型前都重讀原SQL、原Source binding、原host權限及原前置succeeded/attempt=1/真native ACK與結果。原結果、item ID／child canonical及SHA以 DEPENDENCY_RESULTS_JSON 附加到後繼的實際prompt，作任務資料，不升為指令／授權；有效prompt及輸入摘要留原宿主稽核。原保存prompt不改寫。原單項與普通批次執行口也核此門禁，不能繞過未完成依賴。

失敗、取消、Stop、撤權、逾時或UNKNOWN停止後續啟動；原在途清理由原executor處理，未啟動項保持pending。未完成圖回 CODEX_DAG_HALTED_QUERY_ONLY／unstarted_item_ids／unverified_item_ids，不能把未知在途項稱未啟動。cancel_batch取消未開始項與batch_stop停止指定在途項仍沿原流程；不擅停整帳號服務。

## 結果與恢復

執行失回只用 batch_receipt(operation_kind=execute) 查原鍵，不換鍵重跑。成功回 CODEX_DAG_FINISHED、host_dag_enforced=true、children各有observed_execution_verified/original_result_saved。再用**新讀鍵**原batch_status完整查原SQL，核每項succeeded/attempt=1及結果、後繼started_at晚於全部前置completed_at；核後繼輸入SHA與原前置結果一致。模型exit0不等於業務標準或獨立QA已簽。

本版會在Work工具呼叫返回後由原worker繼續已明確交辦的整圖；不依賴Work每階段叫回。Codespace／宿主停止則不保證持續；崩潰／重啟不自動重送UNKNOWN、不重播已取出的圖。原once、原SQL與audit保史，只查原鍵。這不是無限期DAG、跨批次依賴、失敗重試、自動復活、Router動態選路、原生Work派工、固定Agent runtime或真容器沙盒。

## 已驗範圍

原宿主12項工程／原SQL回歸PASS：包含A/B→C真正依賴資料傳遞、直接C繞過拒絕、前置失敗後繼不啟動、假終態無ACK拒絕、圖更改拒絕、防重、撤權及舊批次Stop。CLI/身份是明示synthetic測具。

另有本Work原MCP真Codex三節點驗收PASS：A=803、B=333，宿主自動傳入C得到1136；原SQL獨立查回三項succeeded/attempt=1，C開始晚於A/B完成，CODEX_DAG_FINISHED及輸入摘要一致。沒有借歷史獨立批次充當DAG。跨另一新Work及獨立QA尚未做本版驗收；不得重跑已完成固定測試圖。
