import logging
import requests
from datetime import datetime, timezone
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = "8340283835:AAGdm3lvFrm1iPxvul5ek6Xqnjg0q_45QQc"
SPORTS_API_TOKEN = "RB5JZRt31uOdrJHSIO4llP6Yqh4bjoRfWCYOAAivMs0OdpQR621vrvsiYt60"
BASE_URL = "https://cricket.sportmonks.com/api/v2.0"

TEAM_NAMES = {
    2: "Chennai Super Kings", 3: "Delhi Capitals", 4: "Punjab Kings",
    5: "Kolkata Knight Riders", 6: "Mumbai Indians", 7: "Rajasthan Royals",
    8: "Royal Challengers Bengaluru", 9: "Sunrisers Hyderabad",
    1976: "Gujarat Titans", 1979: "Lucknow Super Giants",
    11: "Karachi Kings", 12: "Quetta Gladiators", 13: "Lahore Qalandars",
    14: "Multan Sultans", 15: "Peshawar Zalmi", 16: "Islamabad United",
    3062: "Rawalpindi"
}
TEAM_CODES = {
    2: "CSK", 3: "DC", 4: "PBKS", 5: "KKR", 6: "MI", 7: "RR",
    8: "RCB", 9: "SRH", 1976: "GT", 1979: "LSG",
    11: "KAR", 12: "QUE", 13: "LAH", 14: "MUL",
    15: "PES", 16: "ISL", 3062: "RAW"
}
IPL_ALL_SEASONS = [444, 441, 101, 98, 95, 92, 2, 423, 708, 932, 1223, 1484, 1689, 1795]
PSL_ALL_SEASONS = [53, 59, 13, 1802]
IPL_SEASON_ID   = 1795
PSL_SEASON_ID   = 1802

_h2h_cache      = {}
_fixtures_cache = {}
_tracking       = {}
_last_fixture   = {}

def api_get(endpoint, params={}):
    p = dict(params)
    p["api_token"] = SPORTS_API_TOKEN
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", params=p, timeout=10)
        return r.json()
    except Exception as e:
        logger.error(f"API error: {e}")
        return None

def get_team_name(tid):
    if tid in TEAM_NAMES: return TEAM_NAMES[tid]
    d = api_get(f"teams/{tid}")
    if d and "data" in d:
        n = d["data"].get("name", f"Team {tid}")
        TEAM_NAMES[tid] = n; return n
    return f"Team {tid}"

def get_team_code(tid):
    return TEAM_CODES.get(tid, get_team_name(tid)[:3].upper())

def get_live_matches():
    d = api_get("livescores")
    return [f for f in d.get("data", []) if f.get("league_id") in [1, 8]] if d else []

def get_upcoming_matches(season_id, limit=6):
    d = api_get("fixtures", {"filter[season_id]": season_id})
    if not d or "data" not in d: return []
    now = datetime.now(timezone.utc)
    out = []
    for f in d["data"]:
        if f.get("status") == "NS":
            try:
                dt = datetime.fromisoformat(f["starting_at"].replace("Z", "+00:00"))
                if dt > now: f["_dt"] = dt; out.append(f)
            except: pass
    out.sort(key=lambda x: x["_dt"])
    return out[:limit]

def get_fixture_detail(fid, include="scoreboards,batting,bowling,lineup"):
    return api_get(f"fixtures/{fid}", {"include": include})

def get_season_fixtures(sid):
    if sid in _fixtures_cache: return _fixtures_cache[sid]
    d = api_get("fixtures", {"filter[season_id]": sid})
    r = d["data"] if d and "data" in d else []
    _fixtures_cache[sid] = r; return r

def get_h2h_stats(t1, t2, league_id=1):
    key = tuple(sorted([t1, t2]))
    if key in _h2h_cache: return _h2h_cache[key]
    seasons = IPL_ALL_SEASONS if league_id == 1 else PSL_ALL_SEASONS
    wins = {t1: 0, t2: 0}; total = 0
    rf = {t1: [], t2: []}
    for sid in seasons:
        for f in get_season_fixtures(sid):
            lt, vt = f.get("localteam_id"), f.get("visitorteam_id")
            if set([lt, vt]) == set([t1, t2]):
                w = f.get("winner_team_id")
                if w in wins:
                    wins[w] += 1; total += 1
                    rf[w].append("W")
                    rf[t2 if w == t1 else t1].append("L")
    r = {"wins": wins, "total": total, "recent_form": {k: v[-5:] for k, v in rf.items()}}
    _h2h_cache[key] = r; return r

def get_team_form(tid, league_id=1):
    seasons = IPL_ALL_SEASONS[-3:] if league_id == 1 else PSL_ALL_SEASONS[-2:]
    res = []
    for sid in seasons:
        for f in get_season_fixtures(sid):
            if f.get("status") != "Finished": continue
            if f.get("localteam_id") == tid or f.get("visitorteam_id") == tid:
                w = f.get("winner_team_id")
                res.append("W" if w == tid else "L" if w else None)
    return [r for r in res if r][-10:]

def score_player(p, batting_data, bowling_data):
    pid = p["id"]
    pos = p.get("position", {}).get("name", "")
    bat  = next((b for b in batting_data  if b.get("player_id") == pid), None)
    bowl = next((b for b in bowling_data if b.get("player_id") == pid), None)
    s = 5.0
    if bat:
        sr = bat.get("rate", 100); score = bat.get("score", 0)
        if sr > 150: s += 1.5
        elif sr > 130: s += 0.5
        elif sr < 80: s -= 1.0
        if score > 40: s += 1.5
        elif score > 20: s += 0.5
    elif "Bowler" not in pos and "Allrounder" not in pos:
        s += 0.5
    if bowl:
        eco = bowl.get("rate", 8); wkts = bowl.get("wickets", 0)
        if eco < 7: s += 1.5
        elif eco < 8.5: s += 0.5
        elif eco > 10: s -= 1.0
        if wkts >= 2: s += 1.5
        elif wkts == 1: s += 0.5
    return round(min(10, max(1, s)), 1)

def get_lineup_strength(lineup, batting_data, bowling_data):
    tp = defaultdict(list)
    for p in lineup:
        tid = p.get("lineup", {}).get("team_id")
        tp[tid].append(p)
    ta = {}
    for tid, players in tp.items():
        total_s = 0; batters = []; bowlers = []
        for p in players:
            fs = score_player(p, batting_data, bowling_data)
            total_s += fs
            name = p.get("fullname", str(p["id"]))
            pos  = p.get("position", {}).get("name", "")
            cap  = " (C)" if p.get("lineup", {}).get("captain") else " (WK)" if p.get("lineup", {}).get("wicketkeeper") else ""
            stars = "⭐" * int(fs / 2)
            bat  = next((b for b in batting_data  if b.get("player_id") == p["id"]), None)
            bowl = next((b for b in bowling_data if b.get("player_id") == p["id"]), None)
            e = {"name": name+cap, "form_score": fs, "stars": stars,
                 "score": bat.get("score","-")    if bat  else "-",
                 "balls": bat.get("ball","-")     if bat  else "-",
                 "sr":    bat.get("rate","-")     if bat  else "-",
                 "overs": bowl.get("overs","-")   if bowl else "-",
                 "wkts":  bowl.get("wickets","-") if bowl else "-",
                 "eco":   bowl.get("rate","-")    if bowl else "-",
                 "is_bowler":  bowl is not None or "Bowler" in pos or "Allrounder" in pos,
                 "is_batsman": bat  is not None or "Bowler" not in pos}
            if e["is_batsman"]: batters.append(e)
            if e["is_bowler"]:  bowlers.append(e)
        batters.sort(key=lambda x: x["form_score"], reverse=True)
        bowlers.sort(key=lambda x: x["form_score"], reverse=True)
        ta[tid] = {"top_batters": batters[:3], "top_bowlers": bowlers[:3],
                   "strength": round(total_s/len(players), 2) if players else 5.0}
    return ta

def parse_scoreboards(fd):
    sb = {}
    for s in fd.get("scoreboards", []):
        if s["type"] == "total":
            sb[s["scoreboard"]] = {"team_id": s["team_id"], "total": s["total"],
                                   "overs": s["overs"], "wickets": s["wickets"]}
    return sb

def parse_batting(fd, sc="S2"):
    return [b for b in fd.get("batting", []) if b["scoreboard"] == sc and b.get("active")]

def parse_bowling(fd, sc="S2"):
    bw = [b for b in fd.get("bowling", []) if b["scoreboard"] == sc]
    bw.sort(key=lambda x: x.get("updated_at",""), reverse=True)
    return bw[:1]

def ov_to_balls(ov):
    ov = float(ov)
    return int(ov) * 6 + int(round((ov % 1) * 10))

def should_update(co, lo):
    co = float(co); lo = float(lo) if lo is not None else -1
    cb = ov_to_balls(co); lb = ov_to_balls(lo)
    labels = {
        36: "⚡ After Powerplay (Over 6)",
        60: "🏁 Mid Innings (Over 10)",
        96: "💀 Death Overs (Over 16)",
        120: "🏁 Innings Complete"
    }
    if cb - lb >= 3 and cb > 0:
        ov_int = int(co)
        ball   = int(round((co % 1) * 10))
        label  = labels.get(cb, f"Over {ov_int}.{ball}" if ball else f"Over {ov_int}")
        return True, label
    return False, ""
def predict_pre_match(lid, vid, league_id=1, toss=None, elected=None):
    h2h = get_h2h_stats(lid, vid, league_id)
    lf  = get_team_form(lid, league_id)
    vf  = get_team_form(vid, league_id)
    total = h2h["total"]
    lp = vp = 0.5
    if total > 0:
        lp = 0.65*lp + 0.35*(h2h["wins"].get(lid,0)/total)
        vp = 0.65*vp + 0.35*(h2h["wins"].get(vid,0)/total)
    if lf: lp = 0.80*lp + 0.20*(lf.count("W")/len(lf))
    if vf: vp = 0.80*vp + 0.20*(vf.count("W")/len(vf))
    if toss == lid:   lp += 0.04; vp -= 0.04
    elif toss == vid: vp += 0.04; lp -= 0.04
    if elected == "batting" and toss == lid:  lp += 0.02
    elif elected == "batting" and toss == vid: vp += 0.02
    t = lp+vp; lp = round((lp/t)*100, 1)
    return {"local_prob": lp, "visitor_prob": round(100-lp, 1),
            "h2h_total": total,
            "h2h_local_wins": h2h["wins"].get(lid, 0),
            "h2h_visitor_wins": h2h["wins"].get(vid, 0),
            "recent_h2h": h2h["recent_form"],
            "local_form": lf, "visitor_form": vf}

def predict_with_lineup(lid, vid, lineup, batting_data, bowling_data, league_id, toss=None, elected=None):
    base = predict_pre_match(lid, vid, league_id, toss, elected)
    ta   = get_lineup_strength(lineup, batting_data, bowling_data)
    la   = ta.get(lid, {}); va = ta.get(vid, {})
    ls   = la.get("strength", 5.0); vs = va.get("strength", 5.0)
    lp   = base["local_prob"]/100; vp = base["visitor_prob"]/100
    ts   = ls+vs if ls+vs > 0 else 10
    lp   = 0.75*lp + 0.25*(ls/ts); vp = 0.75*vp + 0.25*(vs/ts)
    t    = lp+vp; lp = round((lp/t)*100, 1)
    return {"local_prob": lp, "visitor_prob": round(100-lp, 1),
            "base_pred": base, "local_analysis": la, "visitor_analysis": va,
            "local_strength": ls, "visitor_strength": vs}

def predict_innings_live(sb, key="S1"):
    s = sb.get(key)
    if not s: return None
    ov = float(s["overs"]) if s["overs"] else 0
    if ov == 0: return None
    balls = ov_to_balls(ov)
    if balls == 0: return None
    crr  = round((s["total"] / balls) * 6, 2)
    proj = int(crr * 20)
    wl   = 10 - s["wickets"]
    if wl<=3: proj=int(proj*0.85)
    elif wl<=5: proj=int(proj*0.92)
    elif wl>=9: proj=int(proj*1.05)
    return {"total":s["total"],"overs":ov,"wickets":s["wickets"],
            "crr":crr,"projected":proj,"wickets_left":wl,"team_id":s["team_id"]}

def predict_mid_match(sb):
    s1=sb.get("S1"); s2=sb.get("S2")
    if not s1 or not s2: return None
    target=s1["total"]+1; cur=s2["total"]
    ov=float(s2["overs"]); balls=ov_to_balls(ov)
    if balls==0: return None
    wr=10-s2["wickets"]; balls_rem=120-balls; rn=target-cur
    crr=round((cur/balls)*6,2); rrr=round((rn/balls_rem)*6,2) if balls_rem>0 else 99
    od=round(ov,1); orr=round(balls_rem/6,1)
    base=50-(rrr-crr)*5-(10-wr)*2+wr*1.5
    if ov<=6 and crr>rrr: base+=5
    if wr>=8 and rrr<10:  base+=8
    if wr<=3: base-=15
    if rn<=0: base=95
    cp=round(min(95,max(5,base)),1)
    return {"target":target,"current":cur,"overs_done":od,
            "runs_needed":rn,"overs_remaining":orr,
            "wickets_remaining":wr,"crr":crr,"rrr":rrr,
            "in_powerplay":ov<=6,"in_death":ov>=16,
            "chase_prob":cp,"defend_prob":round(100-cp,1),
            "chasing_team":s2["team_id"],"defending_team":s1["team_id"]}

def predict_post_innings(sb):
    s1=sb.get("S1")
    if not s1: return None
    target=s1["total"]+1; ov=s1["overs"]
    rr=round(s1["total"]/float(ov),2) if ov else 0
    cp=round(min(85,max(15,72-(target-160)*0.6)),1)
    return {"target":target,"first_innings_score":s1["total"],
            "first_innings_wickets":s1["wickets"],"run_rate":rr,
            "chase_prob":cp,"defend_prob":round(100-cp,1),"batting_team":s1["team_id"]}

def _cb(c): return "🟩"*int(c/20)+"⬜"*(5-int(c/20))
def pb(p, e="🟩"): return e*int(p/10)+"⬜"*(10-int(p/10))
def fstr(fl): return "".join(["✅" if r=="W" else "❌" for r in fl]) or "—"

def bet_sizing(confidence, budget_label="X"):
    if confidence >= 80: pct=25; note="Strong signal"
    elif confidence >= 65: pct=15; note="Decent signal"
    elif confidence >= 50: pct=10; note="Weak signal — bet small"
    else: return None, None, None
    return pct, f"{pct}% of {budget_label}", note

def bet_pre_match(lid, vid, lp, vp, h2h_total, lf, vf):
    gap=abs(lp-vp); fav_c=get_team_code(lid if lp>vp else vid); fav_p=max(lp,vp)
    conf=round((min(100,h2h_total*4)*0.4 + min(100,gap*2)*0.6),1)
    lfr=lf.count("W")/len(lf)*100 if lf else 50
    vfr=vf.count("W")/len(vf)*100 if vf else 50
    if gap<8 or h2h_total<5:
        return (f"💰 *BET ADVISOR* | Pre-Match\n{'═'*28}\n"
                f"🚫 *SKIP* — {'Too close' if gap<8 else 'Low H2H data'}\n"
                f"📊 Confidence: {_cb(conf)} *{conf}%*\n"
                f"💡 Wait for toss + playing 11\n{'═'*28}")
    pct,sz,note = bet_sizing(conf)
    risk="🟢 LOW" if gap>=20 and conf>=60 else "🟡 MEDIUM"
    return (f"💰 *BET ADVISOR* | Pre-Match\n{'═'*28}\n"
            f"✅ *BET → {fav_c}* ({fav_p}% win prob)\n"
            f"📊 H2H:{h2h_total} | Form:{round(lfr)}% vs {round(vfr)}%\n"
            f"💵 Stake: *{sz}* | {note}\n"
            f"⚡ Risk: {risk} | Confidence: {_cb(conf)} *{conf}%*\n"
            f"🚪 Exit if win% drops by 15%+\n{'═'*28}")

def bet_post_lineup(lid, vid, lp, vp, ls, vs):
    gap=abs(lp-vp); str_gap=abs(ls-vs)
    prob_w=lid if lp>vp else vid; str_w=lid if ls>vs else vid
    agree=prob_w==str_w; fav_c=get_team_code(prob_w); fav_p=max(lp,vp)
    conf=round(min(92,gap*1.5+str_gap*5),1)
    if not agree:
        return (f"💰 *BET ADVISOR* | After Playing 11\n{'═'*28}\n"
                f"⚠️ *HOLD* — Conflicting signals\n"
                f"📝 H2H favors one, lineup favors other\n"
                f"⚡ Risk: 🔴 HIGH | Confidence: {_cb(30)} *30%*\n"
                f"💡 Skip this match\n{'═'*28}")
    risk="🟢 LOW" if gap>=15 and str_gap>=0.5 else "🟡 MEDIUM"
    pct,sz,note = bet_sizing(conf)
    if not pct:
        return (f"💰 *BET ADVISOR* | After Playing 11\n{'═'*28}\n"
                f"⚠️ *HOLD* — Confidence too low\n"
                f"⚡ Risk: 🔴 HIGH | Confidence: {_cb(conf)} *{conf}%*\n{'═'*28}")
    return (f"💰 *BET ADVISOR* | After Playing 11\n{'═'*28}\n"
            f"✅ *BET → {fav_c}* ({fav_p}% win prob)\n"
            f"📝 Lineup: {ls:.1f} vs {vs:.1f}\n"
            f"💵 Stake: *{sz}* | {note}\n"
            f"⚡ Risk: {risk} | Confidence: {_cb(conf)} *{conf}%*\n"
            f"🚪 Exit if win% drops by 15%+\n{'═'*28}")

def bet_innings1(proj, crr, wl, ov, batting_id, bowling_id, prev_win_prob=None):
    bc=get_team_code(batting_id); dc=get_team_code(bowling_id)
    flip = f"\n⚠️ Win% flipped {prev_win_prob}% → re-evaluate!" if prev_win_prob else ""
    if ov<4:
        return (f"💰 *BET ADVISOR* | 1st Innings Over {ov}\n{'═'*28}\n"
                f"⏳ *TOO EARLY — WAIT*\n"
                f"📝 Bet only after Over 6\n"
                f"⚡ Risk: 🔴 HIGH | Confidence: {_cb(10)} *10%*\n{'═'*28}")
    if wl>=8 and proj>=185:
        conf=75; pct,sz,note=bet_sizing(conf)
        return (f"💰 *BET ADVISOR* | 1st Innings Over {ov}\n{'═'*28}\n"
                f"✅ *BET → {bc}* (Huge total incoming)\n"
                f"📝 Proj:{proj} | {wl} wkts | CRR:{crr}\n"
                f"💵 Stake: *{sz}* | {note}{flip}\n"
                f"⚡ Risk: 🟢 LOW | Confidence: {_cb(conf)} *{conf}%*\n"
                f"🚪 Exit if proj drops below 165\n{'═'*28}")
    if wl<=4 and proj<=150:
        conf=72; pct,sz,note=bet_sizing(conf)
        return (f"💰 *BET ADVISOR* | 1st Innings Over {ov}\n{'═'*28}\n"
                f"✅ *BET → {dc}* (Low total — bowlers win)\n"
                f"📝 Proj:{proj} | Only {wl} wkts left\n"
                f"💵 Stake: *{sz}* | {note}{flip}\n"
                f"⚡ Risk: 🟢 LOW | Confidence: {_cb(conf)} *{conf}%*\n"
                f"🚪 Exit if proj rises above 165\n{'═'*28}")
    conf=round(min(65,30+(float(ov)-4)*3),1)
    return (f"💰 *BET ADVISOR* | 1st Innings Over {ov}\n{'═'*28}\n"
            f"⚠️ *HOLD* — Proj:{proj} | {wl} wkts left\n"
            f"⚡ Risk: 🟡 MEDIUM | Confidence: {_cb(conf)} *{conf}%*\n"
            f"💡 Best entry at innings break\n{'═'*28}")

def bet_innings_break(target, cp, dp, chasing_id, batting_id):
    gap=abs(cp-dp); cc=get_team_code(chasing_id); bc=get_team_code(batting_id)
    fav=cc if cp>dp else bc; fav_p=max(cp,dp)
    diff=("Easy chase" if target<155 else "Moderate" if target<175
          else "Tough chase" if target<195 else "Very tough chase")
    conf=round(min(90,50+gap),1)
    risk="🟢 LOW" if gap>=20 else "🟡 MEDIUM" if gap>=10 else "🔴 HIGH"
    if gap<8:
        return (f"💰 *BET ADVISOR* | ⭐ Innings Break\n{'═'*28}\n"
                f"⚠️ *HOLD* — Too close ({gap}% gap)\n"
                f"📝 Target:{target} ({diff})\n"
                f"⚡ Risk: 🔴 HIGH | Confidence: {_cb(conf)} *{conf}%*\n{'═'*28}")
    pct,sz,note=bet_sizing(conf)
    rebet="💡 *RE-BET* — add 10% if win% increases 10%+" if conf>=70 else ""
    return (f"💰 *BET ADVISOR* | ⭐ Innings Break (BEST TIME)\n{'═'*28}\n"
            f"✅ *BET → {fav}* ({fav_p}% win prob)\n"
            f"📝 Target:{target} | {diff}\n"
            f"💵 Stake: *{sz}* | {note}\n"
            f"⚡ Risk: {risk} | Confidence: {_cb(conf)} *{conf}%*\n"
            f"🚪 Exit if win% swings 15%+ against you\n"
            f"{rebet}\n{'═'*28}")

def bet_innings2(cp, dp, rrr, crr, wr, od, chasing_id, defending_id, prev_cp=None):
    cc=get_team_code(chasing_id); dc=get_team_code(defending_id)
    gap=abs(cp-dp); fav=cc if cp>dp else dc; fav_p=max(cp,dp)
    conf=round(min(90,30+gap*0.8+min(40,float(od)*2)),1)
    risk="🟢 LOW" if gap>=25 else "🟡 MEDIUM" if gap>=12 else "🔴 HIGH"
    flip=""
    if prev_cp:
        delta=round(cp-prev_cp,1)
        if abs(delta)>=15: flip=f"\n⚠️ *Win% shifted {delta:+.1f}%* — {'Re-BET ✅' if delta>0 and cp>dp else 'EXIT now 🚪'}"
    if wr<=2 and rrr>crr+2:
        return (f"💰 *BET ADVISOR* | 2nd Innings Over {od}\n{'═'*28}\n"
                f"✅ *BET → {dc}* (Defending wins)\n"
                f"📝 Only {wr} wkts | RRR:{rrr} >> CRR:{crr}\n"
                f"💵 Stake: *25% of X* | Strong signal{flip}\n"
                f"⚡ Risk: 🟢 LOW | Confidence: {_cb(88)} *88%*\n{'═'*28}")
    if wr>=8 and crr>rrr+1.5:
        return (f"💰 *BET ADVISOR* | 2nd Innings Over {od}\n{'═'*28}\n"
                f"✅ *BET → {cc}* (Chase cruising)\n"
                f"📝 {wr} wkts | CRR:{crr} > RRR:{rrr}\n"
                f"💵 Stake: *25% of X* | Strong signal{flip}\n"
                f"⚡ Risk: 🟢 LOW | Confidence: {_cb(85)} *85%*\n{'═'*28}")
    if gap<10:
        return (f"💰 *BET ADVISOR* | 2nd Innings Over {od}\n{'═'*28}\n"
                f"⚠️ *HOLD* — {cp}% vs {dp}% | RRR:{rrr} CRR:{crr}{flip}\n"
                f"⚡ Risk: 🔴 HIGH | Confidence: {_cb(conf)} *{conf}%*\n"
                f"💡 Wait 3 more balls\n{'═'*28}")
    pct,sz,note=bet_sizing(conf)
    rebet="\n💡 *RE-BET* — add 10% if trend continues" if conf>=70 else ""
    return (f"💰 *BET ADVISOR* | 2nd Innings Over {od}\n{'═'*28}\n"
            f"✅ *BET → {fav}* ({fav_p}% win prob)\n"
            f"📝 RRR:{rrr} | CRR:{crr} | {wr} wkts{flip}\n"
            f"💵 Stake: *{sz}* | {note}\n"
            f"⚡ Risk: {risk} | Confidence: {_cb(conf)} *{conf}%*\n"
            f"🚪 Exit if win% drops 15%+{rebet}\n{'═'*28}")

def refresh_kb(fixture_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{fixture_id}")
    ]])

def format_pre_match(fixture, pred):
    lc=get_team_code(fixture["localteam_id"]); vc=get_team_code(fixture["visitorteam_id"])
    lt=get_team_name(fixture["localteam_id"]);  vt=get_team_name(fixture["visitorteam_id"])
    try:
        dt=datetime.fromisoformat(fixture["starting_at"].replace("Z","+00:00"))
        ist=dt.strftime("%d %b %Y, %I:%M %p IST")
    except: ist=fixture.get("starting_at","")[:10]
    tl=""
    if fixture.get("toss_won_team_id"):
        tc=get_team_code(fixture["toss_won_team_id"])
        tl=f"\n🪙 *Toss:* {tc} won, elected to *{fixture.get('elected','—')}*"
    h=pred["recent_h2h"]
    return (f"🏏 *PRE-MATCH PREDICTION*\n{'─'*28}\n"
            f"⚔️  *{lt}* vs *{vt}*\n📅  {ist}{tl}\n\n"
            f"📊 *H2H* ({pred['h2h_total']} matches)\n"
            f"  {lc}:{pred['h2h_local_wins']}W  |  {vc}:{pred['h2h_visitor_wins']}W\n\n"
            f"🔥 *Recent H2H* (last 5)\n"
            f"  {lc}: {fstr(h.get(fixture['localteam_id'],[]))}\n"
            f"  {vc}: {fstr(h.get(fixture['visitorteam_id'],[]))}\n\n"
            f"📈 *Form* (last 10)\n"
            f"  {lc}: {fstr(pred['local_form'][-5:])}\n"
            f"  {vc}: {fstr(pred['visitor_form'][-5:])}\n\n"
            f"🎯 *Win Probability*\n"
            f"  {pb(pred['local_prob'],'🟩')} {lc}: *{pred['local_prob']}%*\n"
            f"  {pb(pred['visitor_prob'],'🟦')} {vc}: *{pred['visitor_prob']}%*")

def format_lineup_prediction(fixture, pred):
    lc=get_team_code(fixture["localteam_id"]); vc=get_team_code(fixture["visitorteam_id"])
    lt=get_team_name(fixture["localteam_id"]);  vt=get_team_name(fixture["visitorteam_id"])
    la=pred["local_analysis"]; va=pred["visitor_analysis"]
    tl=""
    if fixture.get("toss_won_team_id"):
        tc=get_team_code(fixture["toss_won_team_id"])
        tl=f"\n🪙 *Toss:* {tc} won, elected to *{fixture.get('elected','—')}*"
    def ts(name, a):
        lines=f"\n🔵 *{name}* (Strength:{a.get('strength',0):.1f}/10)\n"
        if a.get("top_batters"):
            lines+="  🏏 *Batters:*\n"
            for p in a["top_batters"]:
                sc=f"{p['score']}({p['balls']})" if p['score']!="-" else "—"
                lines+=f"  • {p['name']} {p['stars']} | {sc}\n"
        if a.get("top_bowlers"):
            lines+="  🎳 *Bowlers:*\n"
            for p in a["top_bowlers"]:
                bw=f"{p['overs']}ov {p['wkts']}w Eco:{p['eco']}" if p['overs']!="-" else "—"
                lines+=f"  • {p['name']} {p['stars']} | {bw}\n"
        return lines
    return (f"🏟️ *PLAYING 11*\n{'─'*28}\n⚔️  *{lt}* vs *{vt}*{tl}\n"
            f"{ts(lt,la)}{ts(vt,va)}\n{'─'*28}\n"
            f"🎯 *Win %*\n"
            f"  {pb(pred['local_prob'],'🟩')} {lc}: *{pred['local_prob']}%*\n"
            f"  {pb(pred['visitor_prob'],'🟦')} {vc}: *{pred['visitor_prob']}%*")

def format_innings1(f, pred, label, fid):
    tc=get_team_code(pred["team_id"]); ov=pred["overs"]
    ph="⚡ POWERPLAY" if ov<=6 else "💀 DEATH" if ov>=16 else "🏃 MIDDLE"
    we="🟢" if pred["wickets_left"]>=7 else "🟡" if pred["wickets_left"]>=4 else "🔴"
    return (f"🔴 *1ST INNINGS* | {ph}\n📍 *{label}*\n{'─'*28}\n"
            f"🏏 *{tc}:* {pred['total']}/{pred['wickets']} ({ov}ov)\n"
            f"💨 CRR:{pred['crr']} | 📈 Proj:*{pred['projected']}*\n"
            f"{we} Wkts in hand:*{pred['wickets_left']}*")

def format_innings2(f, pred, batting, bowling, label):
    cc=get_team_code(pred["chasing_team"]); dc=get_team_code(pred["defending_team"])
    ph="⚡ PP" if pred["in_powerplay"] else "💀 DEATH" if pred["in_death"] else "🏃 MID"
    rt="🟢" if pred["rrr"]<=pred["crr"] else "🔴"
    pr=("🟢 Comfortable" if pred["rrr"]<pred["crr"]
        else "🟡 Manageable" if pred["rrr"]-pred["crr"]<2
        else "🟠 Pressure" if pred["rrr"]-pred["crr"]<4 else "🔴 High Pressure")
    we="🟢" if pred["wickets_remaining"]>=7 else "🟡" if pred["wickets_remaining"]>=4 else "🔴"
    bm="".join([f"  🏏 {b['score']}({b['ball']}) SR:{b['rate']}\n" for b in batting[:2]]) or "  —\n"
    bw="".join([f"  🎳 {b['overs']}ov {b['runs']}r {b['wickets']}w Eco:{b['rate']}\n" for b in bowling[:1]]) or "  —\n"
    return (f"🔴 *LIVE* | {ph}\n📍 *{label}*\n{'─'*28}\n"
            f"⚔️  *{cc}* vs *{dc}*\n"
            f"🎯 Target:{pred['target']} | Need:*{pred['runs_needed']} off {pred['overs_remaining']}ov*\n"
            f"📊 {pred['current']}/{10-pred['wickets_remaining']} ({pred['overs_done']}ov)\n"
            f"💨 CRR:{pred['crr']} {rt} RRR:{pred['rrr']} | {we} Wkts:{pred['wickets_remaining']}\n"
            f"📌 {pr}\n🏏{bm}🎳{bw}"
            f"🎯 {pb(pred['chase_prob'],'🟦')}{cc}:{pred['chase_prob']}% | "
            f"{pb(pred['defend_prob'],'🟩')}{dc}:{pred['defend_prob']}%")

def format_innings_break(f, pred):
    bc=get_team_code(pred["batting_team"])
    cid=f["visitorteam_id"] if pred["batting_team"]==f["localteam_id"] else f["localteam_id"]
    cc=get_team_code(cid)
    diff=("🟢 Easy" if pred["target"]<155 else "🟡 Moderate" if pred["target"]<175
          else "🟠 Tough" if pred["target"]<195 else "🔴 Very Tough")
    return (f"🏏 *INNINGS BREAK*\n{'─'*28}\n"
            f"📊 *{bc}:* {pred['first_innings_score']}/{pred['first_innings_wickets']} (20ov) | RR:{pred['run_rate']}\n"
            f"🎯 Target for *{cc}*: {pred['target']} | {diff}\n"
            f"🎯 {pb(pred['chase_prob'],'🟦')}{cc}:{pred['chase_prob']}% | "
            f"{pb(pred['defend_prob'],'🟩')}{bc}:{pred['defend_prob']}%")
async def track_match(context, chat_id, fixture_id):
    track = _tracking.get(chat_id, {})
    last_over   = track.get("last_over", -1)
    last_status = track.get("last_status", "")
    prev_cp     = track.get("prev_chase_prob", None)

    resp = get_fixture_detail(fixture_id)
    if not resp or "data" not in resp: return
    f = resp["data"]
    status = f.get("status", "")
    lid = f["localteam_id"]; vid = f["visitorteam_id"]
    league_id = f.get("league_id", 1)
    sb = parse_scoreboards(f)
    fid = fixture_id

    if status == "Finished":
        winner = get_team_name(f.get("winner_team_id")) if f.get("winner_team_id") else "Unknown"
        await context.bot.send_message(chat_id=chat_id,
            text=f"🏆 *MATCH RESULT*\n{'─'*28}\n🏆 *Winner: {winner}*\n\n{f.get('note','')}",
            parse_mode="Markdown")
        _tracking.pop(chat_id, None); return

    lineup = f.get("lineup", [])
    if lineup and not track.get("lineup_sent") and f.get("toss_won_team_id"):
        _tracking[chat_id]["lineup_sent"] = True
        await context.bot.send_message(chat_id=chat_id,
            text="⏳ Toss done! Analysing playing 11...", parse_mode="Markdown")
        pred = predict_with_lineup(lid, vid, lineup,
                                   f.get("batting",[]), f.get("bowling",[]),
                                   league_id, f.get("toss_won_team_id"), f.get("elected"))
        await context.bot.send_message(chat_id=chat_id,
            text=format_lineup_prediction(f, pred), parse_mode="Markdown",
            reply_markup=refresh_kb(fid))
        await context.bot.send_message(chat_id=chat_id,
            text=bet_post_lineup(lid, vid, pred["local_prob"], pred["visitor_prob"],
                                 pred["local_strength"], pred["visitor_strength"]),
            parse_mode="Markdown")

    if status == "1st Innings":
        s1 = sb.get("S1")
        if s1:
            co = float(s1["overs"]) if s1["overs"] else 0
            upd, label = should_update(co, last_over)
            if upd:
                pred = predict_innings_live(sb, "S1")
                if pred:
                    await context.bot.send_message(chat_id=chat_id,
                        text=format_innings1(f, pred, label, fid), parse_mode="Markdown",
                        reply_markup=refresh_kb(fid))
                    await context.bot.send_message(chat_id=chat_id,
                        text=bet_innings1(pred["projected"], pred["crr"],
                                         pred["wickets_left"], pred["overs"], lid, vid),
                        parse_mode="Markdown")
                _tracking[chat_id].update({"last_over": co, "last_status": status})

    elif status == "Innings Break" and last_status != "Innings Break":
        pred = predict_post_innings(sb)
        if pred:
            cid = vid if pred["batting_team"]==lid else lid
            await context.bot.send_message(chat_id=chat_id,
                text=format_innings_break(f, pred), parse_mode="Markdown",
                reply_markup=refresh_kb(fid))
            await context.bot.send_message(chat_id=chat_id,
                text=bet_innings_break(pred["target"], pred["chase_prob"],
                                       pred["defend_prob"], cid, lid),
                parse_mode="Markdown")
        _tracking[chat_id].update({"last_over": -1, "last_status": status,
                                   "prev_chase_prob": pred["chase_prob"] if pred else None})

    elif status == "2nd Innings":
        s2 = sb.get("S2")
        if s2:
            co = float(s2["overs"]) if s2["overs"] else 0
            upd, label = should_update(co, last_over)
            if upd:
                pred = predict_mid_match(sb)
                if pred:
                    await context.bot.send_message(chat_id=chat_id,
                        text=format_innings2(f, pred,
                            parse_batting(f,"S2"), parse_bowling(f,"S2"), label),
                        parse_mode="Markdown", reply_markup=refresh_kb(fid))
                    await context.bot.send_message(chat_id=chat_id,
                        text=bet_innings2(pred["chase_prob"], pred["defend_prob"],
                                         pred["rrr"], pred["crr"],
                                         pred["wickets_remaining"], pred["overs_done"],
                                         pred["chasing_team"], pred["defending_team"], prev_cp),
                        parse_mode="Markdown")
                _tracking[chat_id].update({"last_over": co, "last_status": status,
                                           "prev_chase_prob": pred["chase_prob"] if pred else prev_cp})

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    kb = [
        [InlineKeyboardButton("🔴 Live Matches", callback_data="live")],
        [InlineKeyboardButton("📅 IPL 2026", callback_data="ipl_upcoming"),
         InlineKeyboardButton("📅 PSL", callback_data="psl_upcoming")],
        [InlineKeyboardButton("🔮 IPL Predict", callback_data="ipl_predict"),
         InlineKeyboardButton("🔮 PSL Predict", callback_data="psl_predict")],
    ]
    if chat_id in _last_fixture:
        kb.append([InlineKeyboardButton("▶️ Resume Last Match", callback_data="resume")])
    await update.message.reply_text(
        "🏏 *Cricket Prediction Bot*\n_Powered by Sportmonks_\n\nChoose an option:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    matches = get_live_matches()
    if not matches:
        await msg.reply_text("❌ No live IPL/PSL matches right now."); return
    kb = [[InlineKeyboardButton(
        f"🔴 {get_team_code(m['localteam_id'])} vs {get_team_code(m['visitorteam_id'])} — {m.get('status','')}",
        callback_data=f"match_{m['id']}")] for m in matches]
    await msg.reply_text("*🔴 Live Matches:*", parse_mode="Markdown",
                         reply_markup=InlineKeyboardMarkup(kb))

async def handle_match(update: Update, context: ContextTypes.DEFAULT_TYPE, fixture_id: int):
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()
    await query.message.reply_text("⏳ Analysing match data...")

    resp = get_fixture_detail(fixture_id)
    if not resp or "data" not in resp:
        await query.message.reply_text("❌ Could not fetch match details."); return

    f = resp["data"]
    status   = f.get("status","NS")
    lid      = f["localteam_id"]; vid = f["visitorteam_id"]
    league_id= f.get("league_id",1)
    sb       = parse_scoreboards(f)
    lineup   = f.get("lineup",[])
    bat_data = f.get("batting",[])
    bowl_data= f.get("bowling",[])
    fid      = fixture_id

    _last_fixture[chat_id] = fixture_id

    pred_pre = predict_pre_match(lid, vid, league_id,
                                 f.get("toss_won_team_id"), f.get("elected"))
    await query.message.reply_text(format_pre_match(f, pred_pre),
                                   parse_mode="Markdown", reply_markup=refresh_kb(fid))
    await query.message.reply_text(
        bet_pre_match(lid, vid, pred_pre["local_prob"], pred_pre["visitor_prob"],
                      pred_pre["h2h_total"], pred_pre["local_form"], pred_pre["visitor_form"]),
        parse_mode="Markdown")

    if lineup and f.get("toss_won_team_id"):
        await query.message.reply_text("⏳ Analysing playing 11...")
        pred_l = predict_with_lineup(lid, vid, lineup, bat_data, bowl_data,
                                     league_id, f.get("toss_won_team_id"), f.get("elected"))
        await query.message.reply_text(format_lineup_prediction(f, pred_l),
                                       parse_mode="Markdown", reply_markup=refresh_kb(fid))
        await query.message.reply_text(
            bet_post_lineup(lid, vid, pred_l["local_prob"], pred_l["visitor_prob"],
                            pred_l["local_strength"], pred_l["visitor_strength"]),
            parse_mode="Markdown")

    if status == "1st Innings":
        pred = predict_innings_live(sb,"S1")
        if pred:
            ov = sb.get("S1",{}).get("overs",0)
            await query.message.reply_text(format_innings1(f,pred,f"Over {ov}",fid),
                                           parse_mode="Markdown", reply_markup=refresh_kb(fid))
            await query.message.reply_text(
                bet_innings1(pred["projected"],pred["crr"],pred["wickets_left"],pred["overs"],lid,vid),
                parse_mode="Markdown")
    elif status == "2nd Innings":
        pred = predict_mid_match(sb)
        if pred:
            await query.message.reply_text(
                format_innings2(f,pred,parse_batting(f),parse_bowling(f),"Current"),
                parse_mode="Markdown", reply_markup=refresh_kb(fid))
            await query.message.reply_text(
                bet_innings2(pred["chase_prob"],pred["defend_prob"],pred["rrr"],pred["crr"],
                             pred["wickets_remaining"],pred["overs_done"],
                             pred["chasing_team"],pred["defending_team"]),
                parse_mode="Markdown")
    elif status == "Innings Break":
        pred = predict_post_innings(sb)
        if pred:
            cid = vid if pred["batting_team"]==lid else lid
            await query.message.reply_text(format_innings_break(f,pred),
                                           parse_mode="Markdown", reply_markup=refresh_kb(fid))
            await query.message.reply_text(
                bet_innings_break(pred["target"],pred["chase_prob"],pred["defend_prob"],cid,lid),
                parse_mode="Markdown")
    elif status == "Finished":
        winner = get_team_name(f.get("winner_team_id")) if f.get("winner_team_id") else "Unknown"
        await query.message.reply_text(
            f"🏆 *MATCH RESULT*\n{'─'*28}\n🏆 *Winner: {winner}*\n\n{f.get('note','')}",
            parse_mode="Markdown")
        return

    s_key = "S2" if status=="2nd Innings" else "S1"
    co = float(sb.get(s_key,{}).get("overs",0)) if sb.get(s_key) else 0
    _tracking[chat_id] = {"fixture_id":fid,"last_over":co,"last_status":status,
                           "lineup_sent": bool(lineup and f.get("toss_won_team_id")),
                           "prev_chase_prob": None}
    jn = f"track_{chat_id}"
    for job in context.job_queue.get_jobs_by_name(jn): job.schedule_removal()
    context.job_queue.run_repeating(
        lambda ctx: track_match(ctx, chat_id, fid),
        interval=30, first=30, name=jn)
    await query.message.reply_text(
        "✅ *Auto-tracking ON*\nUpdates every 3 balls automatically.\nSend /stop to stop.",
        parse_mode="Markdown")

async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id if update.message else update.callback_query.message.chat_id
    fid = _last_fixture.get(chat_id)
    if not fid:
        msg = update.message or update.callback_query.message
        await msg.reply_text("❌ No previous match. Use /start."); return
    _tracking[chat_id] = {"fixture_id":fid,"last_over":-1,"last_status":"",
                           "lineup_sent":False,"prev_chase_prob":None}
    jn = f"track_{chat_id}"
    for job in context.job_queue.get_jobs_by_name(jn): job.schedule_removal()
    context.job_queue.run_repeating(
        lambda ctx: track_match(ctx, chat_id, fid),
        interval=30, first=5, name=jn)
    msg = update.message or update.callback_query.message
    await msg.reply_text("▶️ *Resuming last match tracking...*", parse_mode="Markdown")

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    for job in context.job_queue.get_jobs_by_name(f"track_{chat_id}"): job.schedule_removal()
    _tracking.pop(chat_id, None)
    await update.message.reply_text("🛑 Tracking stopped.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data == "live":
        await live_cmd(update, context)
    elif data == "resume":
        await resume_cmd(update, context)
    elif data == "ipl_upcoming":
        matches = get_upcoming_matches(IPL_SEASON_ID)
        if not matches: await query.message.reply_text("No upcoming IPL."); return
        kb=[[InlineKeyboardButton(
            f"📅 {m['starting_at'][5:10]} | {get_team_code(m['localteam_id'])} vs {get_team_code(m['visitorteam_id'])}",
            callback_data=f"match_{m['id']}")] for m in matches]
        await query.message.reply_text("*📅 Upcoming IPL 2026:*", parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(kb))
    elif data == "psl_upcoming":
        matches = get_upcoming_matches(PSL_SEASON_ID)
        if not matches: await query.message.reply_text("No upcoming PSL."); return
        kb=[[InlineKeyboardButton(
            f"📅 {m['starting_at'][5:10]} | {get_team_code(m['localteam_id'])} vs {get_team_code(m['visitorteam_id'])}",
            callback_data=f"match_{m['id']}")] for m in matches]
        await query.message.reply_text("*📅 Upcoming PSL:*", parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(kb))
    elif data == "ipl_predict":
        await query.message.reply_text("⏳ Loading...")
        matches = get_upcoming_matches(IPL_SEASON_ID,6)
        if not matches: await query.message.reply_text("No upcoming IPL."); return
        kb=[[InlineKeyboardButton(
            f"🔮 {m['starting_at'][5:10]} | {get_team_code(m['localteam_id'])} vs {get_team_code(m['visitorteam_id'])}",
            callback_data=f"match_{m['id']}")] for m in matches]
        await query.message.reply_text("*🔮 Select match:*", parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(kb))
    elif data == "psl_predict":
        matches = get_upcoming_matches(PSL_SEASON_ID,6)
        if not matches: await query.message.reply_text("No upcoming PSL."); return
        kb=[[InlineKeyboardButton(
            f"🔮 {m['starting_at'][5:10]} | {get_team_code(m['localteam_id'])} vs {get_team_code(m['visitorteam_id'])}",
            callback_data=f"match_{m['id']}")] for m in matches]
        await query.message.reply_text("*🔮 Select PSL match:*", parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("refresh_"):
        fid = int(data.split("_")[1])
        _last_fixture[chat_id] = fid
        await handle_match(update, context, fid)
    elif data.startswith("match_"):
        await handle_match(update, context, int(data.split("_")[1]))

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("live",   live_cmd))
    app.add_handler(CommandHandler("stop",   stop_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("✅ Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
