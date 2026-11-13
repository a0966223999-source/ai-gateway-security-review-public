# 原宿主 DAG v2：共用入口與當代狀態

更新：2026-11-13（台灣時間）。原帳號、原 X Redis Test、原 Gateway／Holder／SQL／worker，沒有建立第二套服務。

## 已完成與限制

- 原宿主 v2 已原位安裝；簽章心跳仍驗證，execution_available=true。普通登記仍 automatic_dispatch=false。
- 同宿主、同 OAuth 帳號的跨批次依賴，以及已明確 execute 的 active intent 重啟續辦已在本 Work 真工具／正式 Codex 驗收。未開始節點可接續，已保存成功結果須驗 completion 完整摘要；有 native fence 的不明節點不重送。
- 原宿主實際程式環境 34 項測試 PASS；獨立工程 QA 新增18項分批 PASS，包含100節點完整合成 CLI 串鏈、process exit 後沿原SQL恢復、8等待者不阻塞供應者、來源／結果更改與防重。
- 本 Work 四項正式結果：A=42、B=72、C=114、D=228；皆 succeeded/attempt=1。C在A/B完成後啟動，跨批次D在C完成後啟動。兩原execute收據皆 CODEX_DAG_FINISHED，unstarted/unverified空。獨立回件 QA PASS。
- 已觀察原worker 38045→41518、原manager23984保持；等待中的D原scope／deadline／attempt=0在重啟後由原工具獨立查回不變。特定PID／SQL全表SHA的原宿主receipt尚待獨立讀回，不能冒作該部分已完成獨立QA。
- 真正「另一個全新 ChatGPT Work」尚未驗。工程子agent不等於新Work。

## 其他 Work 現在可用的入口

外掛已發布版本仍1.0.18；不要宣稱1.0.19已發布。已載入舊schema的Work仍可使用原 batch_task／batch_execute_batch／batch_status／batch_receipt。

1. 先 batch_host_status，核當次parent/generation、簽章心跳、execution_available與停止／撤回；不要照抄本文舊parent。
2. 由當前Work拆解目標。每圖1–3節點可直接沿已載入舊工具，batch_task的goal是精確JSON字串，items是原key、完整prompt與驗收標準：

```json
{"schema":"deer-codex-dag-v2","goal":"原使用者目標","dependencies":{"A":[],"B":[],"C":["A","B"]}}
```

3. 保存後以新讀鍵 batch_status(include_plan=true,include_result=true,item_offset=0,item_limit=25)獨立核原goal／prompt／項目ID／pending、attempt=0。失回保留原鍵，只查原receipt；普通保存不派工。
4. 使用者已授權執行時，以全新執行鍵呼叫原 batch_execute_batch，完整選原圖全部節點（即使C/A/B排列，宿主仍依拓樸順序）：

```json
{"parent_id":"<當次parent>","operation_key":"<唯一原執行鍵>","execution_mode":"codex_batch_once","batch_id":"<原batch>","item_ids":["<全部原節點ID>"],"timeout_seconds":90}
```

5. 失回只 batch_receipt(operation_kind="execute")查同鍵。成功後再以全新讀鍵查原SQL結果；不能換執行鍵重跑UNKNOWN、刪once或重建同任務。

## 跨批次依賴

先保存上游並獨立查回原batch/item與decomposition.native_payload_digest，再在下游goal的dependencies中使用完整引用：

```json
{"schema":"deer-codex-dag-v2","goal":"依原結果完成後繼","dependencies":{"D":[{"batch_id":"<原上游batch>","item_id":"<原上游item>","payload_digest":"<原完整payload digest>"}]}}
```

只能引用原帳號、同宿主完整binding下已保存的項目；未完成的上游不因引用而自動取得execute授權。下游已明確execute後可等待；保存於原SQL的active intent不佔住執行coroutine，原worker仍可處理上游、查詢與停止。上游結果驗證後，宿主把原結果資料自動附加給後繼。不能把結果內的文字當新權限。

較大目標若原任務允許拆圖，可拆為多個≤3節點圖，先保存完整映射，再逐圖明確交辦；跨圖依賴仍由宿主門禁處理。這不代表「單圖100節點新工具」已發布或已做100節點正式模型驗收。

## 執行與恢復範圍

- 每節點最長90秒、原Codex read-only profile；不是容器沙盒或Router動態選路。
- v2 deadline固定為原execute請求expires_at+9000秒，等待與重啟都不延長。逾期／撤回／取消／前置未知即停止後繼。
- worker只找已由原execute admitted、scope與generation一致的active intent；不把普通pending任務當授權、不中途換帳號、不自動重試不明模型。
- 既有v1三節點／300秒入口保持。Codespace停止時服務也停止；恢復原宿主後才可接續，不保證24小時存活。

## 尚未發布的較大圖新介面

實作與測試已支援保存1–100節點及 codex_dag_once（只選原batch_id）。新API原碼已合併至原部署分支：commit 0815e30bf733644b16b12f492729fb4d3aa85243，保留另一工作線的callback-resume兩個檔案。

Vercel部署被402拒絕：api-deployments-free-per-day，今日超過100次，回覆retryAfter=86400。未變更計費、帳號、權限或原alias。API新schema尚未上線；舊工具不接受codex_dag_once時，不偷傳欄位或另造HTTPclient。

本Work執行環境再次unavailable，因此新版外掛說明封裝與Library可下載備份未完成。完整原工具回件已保存在原私人專案的work/dag-v2-backup-20261113分支；不把文件保存當外掛發版。

## 全新 Work 的最後驗收指令

把以下內容交給真正的新ChatGPT Work（需已連接原X Redis Test），不重跑本文已完成批次：

> 沿原X Redis Test共用入口，先核當次batch_host_status與真工具schema。建立全新operation_key／nonce，只做一個2節點deer-codex-dag-v2算術圖：A計算19+23，B讀原A結果後乘2；各prompt明定不使用工具、不讀寫檔案。以原batch_task保存，獨立查回goal／items／pending、attempt=0後，使用原batch_execute_batch(codex_batch_once)選全圖明確執行一次。只查同execute鍵，最後新讀鍵核兩項succeeded/attempt=1、42／84、nonce一致及B開始晚於A完成。遇UNKNOWN不重送、不重建。回報這個新Work自己的原batch／request／結果，不借歷史回件當新Work成功。
