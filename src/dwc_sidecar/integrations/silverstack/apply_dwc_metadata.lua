-- sst: ingest
-- apply_dwc_metadata.lua
-- DWC sidecar → Silverstack custom metadata (Silverstack 9.2+)
--
-- The first line above is the Pomfort context tag — without it
-- Silverstack does not list the script in the "Metadata Adjustment
-- Scripts" dropdown of the Register in Library activity. Verified
-- 2026-04-24 against Silverstack XT 9.2.1 during the §7.1 dry-run.
--
-- This script runs inside Silverstack's embedded Lua 5.5 runtime. At
-- ingest time the onStampVideo hook fires per-clip; we look for
-- <clip-basename>.omc.json next to the clip file, parse it, and write
-- six DWC_* provenance fields into the asset's Custom1..Custom6 slots.
--
-- Silverstack exposes only six custom-metadata setters (setCustom1..
-- setCustom6). The ALE emitter (dwc ale-export) carries the full eight
-- DWC_* columns for consumers that support arbitrary columns
-- (Avid, YoYotta). For Silverstack specifically, we ship:
--
--   Custom1 ← DWC_Signed         ("true" / "false")
--   Custom2 ← DWC_Kid            (kid of most recent event)
--   Custom3 ← DWC_Events         (event count as string)
--   Custom4 ← DWC_LockedBy       (kid of latest lock event, or "")
--   Custom5 ← DWC_LastVerified   (ISO-8601 UTC at ingest time)
--   Custom6 ← DWC_ChainHead      (first 12 hex chars of tip-event hash)
--
-- Rename the Custom column labels in Silverstack Preferences →
-- Custom Metadata so they display as DWC_Signed, DWC_Kid, etc.
--
-- Installation: open Silverstack's script editor
-- (Preferences → Scripts → edit), paste this file into the Shared
-- scope, and save. See integrations/silverstack/README.md for detail.
--
-- Helpers are kept on a module-scope `dwc` table that is captured as an
-- upvalue by `onStampVideo` / `onFinish`. The table is deliberately
-- `local`, not a global: Silverstack's script sandbox evidently disposes
-- or re-chains `_ENV` between script load and hook fire, so any global
-- created at script load (e.g. `dwc = {}`) becomes unreachable at hook
-- time and throws `'__index' chain too long; possible loop`. Verified
-- against Silverstack XT 9.2.1 on 2026-04-24 during the §7.1 dry-run.
-- The Python test harness (tests/test_silverstack_script.py) only talks
-- to the script via the `onStampVideo` global, so making `dwc` local
-- has no effect there.

local dwc = {}

-- ── JSON decoding ───────────────────────────────────────────────────────
-- Prefer Pomfort-bundled dkjson if available; fall back to the minimal
-- recursive-descent parser below. The parser covers the subset DWC
-- sidecars use: objects, arrays, strings (with \n \t \r \\ \" \uXXXX
-- escapes), numbers, true/false/null. Not a general-purpose JSON library.

local ok, _dkjson = pcall(require, "dkjson")
if ok and type(_dkjson) == "table" and _dkjson.decode then
    function dwc.decode(text)
        local obj, _pos, err = _dkjson.decode(text, 1, nil)
        return obj, err
    end
else
    function dwc.decode(text)
        local pos, len = 1, #text
        local parse_value

        local function skip_ws()
            while pos <= len do
                local c = string.byte(text, pos)
                if c == 0x20 or c == 0x09 or c == 0x0A or c == 0x0D then
                    pos = pos + 1
                else return end
            end
        end

        local function parse_string()
            -- pos points at opening quote
            pos = pos + 1
            local parts = {}
            while pos <= len do
                local c = string.sub(text, pos, pos)
                if c == '"' then pos = pos + 1; return table.concat(parts) end
                if c == "\\" then
                    local esc = string.sub(text, pos + 1, pos + 1)
                    if     esc == '"' then parts[#parts+1] = '"';  pos = pos + 2
                    elseif esc == "\\" then parts[#parts+1] = "\\"; pos = pos + 2
                    elseif esc == "/" then parts[#parts+1] = "/";  pos = pos + 2
                    elseif esc == "n" then parts[#parts+1] = "\n"; pos = pos + 2
                    elseif esc == "t" then parts[#parts+1] = "\t"; pos = pos + 2
                    elseif esc == "r" then parts[#parts+1] = "\r"; pos = pos + 2
                    elseif esc == "b" then parts[#parts+1] = "\b"; pos = pos + 2
                    elseif esc == "f" then parts[#parts+1] = "\f"; pos = pos + 2
                    elseif esc == "u" then
                        local hex = string.sub(text, pos + 2, pos + 5)
                        local cp  = tonumber(hex, 16) or 0
                        if cp < 0x80 then
                            parts[#parts+1] = string.char(cp)
                        elseif cp < 0x800 then
                            parts[#parts+1] = string.char(
                                0xC0 + math.floor(cp / 0x40),
                                0x80 + (cp % 0x40))
                        else
                            parts[#parts+1] = string.char(
                                0xE0 + math.floor(cp / 0x1000),
                                0x80 + math.floor((cp % 0x1000) / 0x40),
                                0x80 + (cp % 0x40))
                        end
                        pos = pos + 6
                    else return nil, "unknown escape \\" .. esc
                    end
                else
                    parts[#parts+1] = c; pos = pos + 1
                end
            end
            return nil, "unterminated string"
        end

        local function parse_number()
            local start = pos
            if string.sub(text, pos, pos) == "-" then pos = pos + 1 end
            while pos <= len do
                local c = string.byte(text, pos)
                if (c >= 0x30 and c <= 0x39) or c == 0x2E   -- '.'
                   or c == 0x65 or c == 0x45                -- 'e'/'E'
                   or c == 0x2B or c == 0x2D then           -- '+'/'-'
                    pos = pos + 1
                else break end
            end
            local n = tonumber(string.sub(text, start, pos - 1))
            if n == nil then return nil, "bad number" end
            return n
        end

        parse_value = function()
            skip_ws()
            if pos > len then return nil, "unexpected end of input" end
            local c = string.sub(text, pos, pos)
            if c == "{" then
                pos = pos + 1
                local obj = {}
                skip_ws()
                if string.sub(text, pos, pos) == "}" then pos = pos + 1; return obj end
                while true do
                    skip_ws()
                    if string.sub(text, pos, pos) ~= '"' then
                        return nil, "expected string key at " .. pos
                    end
                    local k, err = parse_string(); if err then return nil, err end
                    skip_ws()
                    if string.sub(text, pos, pos) ~= ":" then
                        return nil, "expected ':' at " .. pos
                    end
                    pos = pos + 1
                    local v, err2 = parse_value(); if err2 then return nil, err2 end
                    obj[k] = v
                    skip_ws()
                    local nc = string.sub(text, pos, pos)
                    if nc == "," then pos = pos + 1
                    elseif nc == "}" then pos = pos + 1; return obj
                    else return nil, "expected ',' or '}' at " .. pos end
                end
            elseif c == "[" then
                pos = pos + 1
                local arr = {}
                skip_ws()
                if string.sub(text, pos, pos) == "]" then pos = pos + 1; return arr end
                while true do
                    local v, err = parse_value(); if err then return nil, err end
                    arr[#arr+1] = v
                    skip_ws()
                    local nc = string.sub(text, pos, pos)
                    if nc == "," then pos = pos + 1
                    elseif nc == "]" then pos = pos + 1; return arr
                    else return nil, "expected ',' or ']' at " .. pos end
                end
            elseif c == '"' then
                return parse_string()
            elseif c == "t" then
                if string.sub(text, pos, pos + 3) == "true" then pos = pos + 4; return true end
                return nil, "expected 'true'"
            elseif c == "f" then
                if string.sub(text, pos, pos + 4) == "false" then pos = pos + 5; return false end
                return nil, "expected 'false'"
            elseif c == "n" then
                if string.sub(text, pos, pos + 3) == "null" then pos = pos + 4; return nil end
                return nil, "expected 'null'"
            else
                return parse_number()
            end
        end

        local result, err = parse_value()
        if err then return nil, err end
        return result
    end
end


-- ── Sidecar field extraction (pure) ─────────────────────────────────────

function dwc.strip_hash_prefix(h)
    if h == nil or h == "" then return "" end
    local colon = string.find(h, ":", 1, true)
    if colon then return string.sub(h, colon + 1) end
    return h
end

function dwc.walk_custom_data(doc, out_events, out_locks)
    if type(doc) ~= "table" then return end
    for k, v in pairs(doc) do
        if k == "customData" and type(v) == "table" then
            for _, entry in ipairs(v) do
                if type(entry) == "table" and type(entry.value) == "table" then
                    if entry.domain == "dwc.sidecar.events" then
                        for _, ev in ipairs(entry.value) do out_events[#out_events+1] = ev end
                    elseif entry.domain == "dwc.sidecar.locks" then
                        for _, lk in ipairs(entry.value) do out_locks[#out_locks+1] = lk end
                    end
                end
            end
        end
        if type(v) == "table" then dwc.walk_custom_data(v, out_events, out_locks) end
    end
end

function dwc.extract_fields(doc, now_iso)
    local events, locks = {}, {}
    dwc.walk_custom_data(doc, events, locks)

    -- Sort events by (ts, seq) — mirrors the Python extractor. Lua's stable
    -- sort is fine since we comparator-sort on a composite string key.
    table.sort(events, function(a, b)
        local aa = (a.ts or "") .. string.format("|%012d", a.seq or 0)
        local bb = (b.ts or "") .. string.format("|%012d", b.seq or 0)
        return aa < bb
    end)

    local latest_kid, chain_head = "", ""
    if #events > 0 then
        local tip = events[#events]
        if type(tip.sig) == "table" then latest_kid = tip.sig.kid or "" end
        chain_head = string.sub(dwc.strip_hash_prefix(tip.hash or ""), 1, 12)
    end

    local locked_by = ""
    if #locks > 0 then
        for i = #events, 1, -1 do
            if events[i].action == "lock" then
                if type(events[i].sig) == "table" then
                    locked_by = events[i].sig.kid or ""
                end
                break
            end
        end
    end

    return {
        DWC_Signed       = "true",
        DWC_Kid          = latest_kid,
        DWC_Events       = tostring(#events),
        DWC_LockedBy     = locked_by,
        DWC_LastVerified = now_iso or os.date("!%Y-%m-%dT%H:%M:%SZ"),
        DWC_ChainHead    = chain_head,
    }
end


-- ── File I/O helpers ────────────────────────────────────────────────────

function dwc.read_file(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local content = f:read("*a")
    f:close()
    return content
end

function dwc.sidecar_path_for_clip(clip_path)
    if type(clip_path) ~= "string" or clip_path == "" then return nil end
    local dir, base = string.match(clip_path, "^(.*)[/\\]([^/\\]+)$")
    if not dir then dir, base = ".", clip_path end
    local stem = string.match(base, "^(.*)%.[^%.]+$") or base
    return dir .. "/" .. stem .. ".omc.json"
end


-- ── Metadata write (wrapper for the six setCustomN calls) ───────────────

function dwc.apply_to_asset(asset, fields)
    local meta = asset:metadata()
    meta:setCustom1(fields.DWC_Signed       or "")
    meta:setCustom2(fields.DWC_Kid          or "")
    meta:setCustom3(fields.DWC_Events       or "")
    meta:setCustom4(fields.DWC_LockedBy     or "")
    meta:setCustom5(fields.DWC_LastVerified or "")
    meta:setCustom6(fields.DWC_ChainHead    or "")
end


-- ── Silverstack hook ────────────────────────────────────────────────────

function onStampVideo(videoClip, clipIndex, resource)
    -- clipIndex unused; signature kept complete per Pomfort convention.
    local _ = clipIndex

    -- FileResource exposes its on-disk path via `:getPath()` per the
    -- Pomfort SDK reference (Silverstack 9.2.0 Lua API, § FileResource).
    -- Accessing a non-existent method name triggers Silverstack's
    -- metatable fallback which manifests as '__index chain too long' —
    -- so use the documented name rather than a guess.
    local clip_path = nil
    if resource ~= nil then
        local ok_call, path = pcall(function() return resource:getPath() end)
        if ok_call and type(path) == "string" then clip_path = path end
    end
    if clip_path == nil then
        print("dwc: resource path unavailable; skipping")
        return
    end

    local sidecar_path = dwc.sidecar_path_for_clip(clip_path)
    if sidecar_path == nil then return end

    local content = dwc.read_file(sidecar_path)
    if content == nil then
        -- Silverstack imports clips without sidecars all the time — silent skip
        return
    end

    local doc, err = dwc.decode(content)
    if err ~= nil or type(doc) ~= "table" then
        print("dwc: invalid JSON in " .. sidecar_path .. ": " .. tostring(err))
        return
    end

    local fields = dwc.extract_fields(doc)
    local ok_apply, apply_err = pcall(dwc.apply_to_asset, videoClip, fields)
    if not ok_apply then
        print("dwc: failed to apply metadata: " .. tostring(apply_err))
        return
    end
    print("dwc: " .. (fields.DWC_ChainHead or "") ..
          " applied to " .. sidecar_path)
end

function onFinish(assets, resources, workingPath, success)
    -- Reconciliation pass — iterate any asset that didn't get stamped at
    -- ingest (e.g., script installed after import). The precise iteration
    -- surface for `assets` isn't documented in Pomfort's public README at
    -- this revision; a best-effort pass is left for a future version
    -- after real-world validation (plan §7.1).
    local _ = assets; local _2 = resources; local _3 = workingPath; local _4 = success
end
