// 種リストと、種の登録（確認事項 149・150 回目）、種のショートカットキー（154 回目。speciesKeysDialog。このファイルの最後）
//   speciesDialog(opts)          種リストの窓。ツールバーの「種の登録」、設定の窓、自動判定の窓から開く。opts.onClose = 閉じたあとに戻る先
//                                上から、調査地の国 → 登録した種の表 → その国の候補（SpeciesNet が知っている種。国を入れると、すぐ下に出る）
//   speciesRegisterDialog(opts)  「種を登録」。登録のしかたを選ぶ: 1 種ずつ入れる / CSV からまとめて / SpeciesNet の候補から / 日本の哺乳類の一覧。
//                                opts.back = 終わったあとに戻る先（なければ窓を閉じる）、opts.selectAfter = 1 種ずつ入れたとき、いま開いている動画でその種を選ぶ
// 種の名前の出し方（混ざらないように、役割を 3 つに分けてある）:
//   名前      画面に出す。日本語の画面では和名、英語の画面では英名（なければ、もう一方 → 学名）
//   学名      自動判定（SpeciesNet）と Camtrap DP 形式は、学名でつなぐ。入れると、自動判定での対応（種・属・科…）が自動で決まる
//   CSV 表記  CSV の Species の列に書く名前。空なら英名から作る

const SP_METHODS = ['manual', 'csv', 'cand', 'defaults'];

async function speciesReload() {
  S.species = await api('/api/species');
  spRefreshMain();
}
function spRefreshMain() {                 // 種リストが変わったあと、本体の画面（右の欄の種の並び、絞り込み、一覧）を合わせる
  rebuildSpMap(); renderFilters();
  renderSpeciesPanel();                    // 動画を開いていなくても、右の欄の種の並びは入れ替える
  if (S.cur) renderFieldsPanel();
  renderWindow(true);
}

// ---------- 国（調査地の国。ISO 3166-1 の 3 文字。SpeciesNet の geofence の符号） ----------
// 3 文字 → 2 文字の符号。国の名前を、ブラウザの Intl.DisplayNames（2 文字の符号で引く）で、画面の言語で出すためのもの。
// Windows の地域情報（.NET の RegionInfo）から作った 243 と、そこにない 6 つ（ATA・ATF・BVT・ESH・HMD・SGS）。SpeciesNet v4 の geofence に出てくる 249 の符号と同じ集まり
const SP_A3_A2 = Object.fromEntries('ABW=AW,AFG=AF,AGO=AO,AIA=AI,ALA=AX,ALB=AL,AND=AD,ARE=AE,ARG=AR,ARM=AM,ASM=AS,ATA=AQ,ATF=TF,ATG=AG,AUS=AU,AUT=AT,AZE=AZ,BDI=BI,BEL=BE,BEN=BJ,BES=BQ,BFA=BF,BGD=BD,BGR=BG,BHR=BH,BHS=BS,BIH=BA,BLM=BL,BLR=BY,BLZ=BZ,BMU=BM,BOL=BO,BRA=BR,BRB=BB,BRN=BN,BTN=BT,BVT=BV,BWA=BW,CAF=CF,CAN=CA,CCK=CC,CHE=CH,CHL=CL,CHN=CN,CIV=CI,CMR=CM,COD=CD,COG=CG,COK=CK,COL=CO,COM=KM,CPV=CV,CRI=CR,CUB=CU,CUW=CW,CXR=CX,CYM=KY,CYP=CY,CZE=CZ,DEU=DE,DJI=DJ,DMA=DM,DNK=DK,DOM=DO,DZA=DZ,ECU=EC,EGY=EG,ERI=ER,ESH=EH,ESP=ES,EST=EE,ETH=ET,FIN=FI,FJI=FJ,FLK=FK,FRA=FR,FRO=FO,FSM=FM,GAB=GA,GBR=GB,GEO=GE,GGY=GG,GHA=GH,GIB=GI,GIN=GN,GLP=GP,GMB=GM,GNB=GW,GNQ=GQ,GRC=GR,GRD=GD,GRL=GL,GTM=GT,GUF=GF,GUM=GU,GUY=GY,HKG=HK,HMD=HM,HND=HN,HRV=HR,HTI=HT,HUN=HU,IDN=ID,IMN=IM,IND=IN,IOT=IO,IRL=IE,IRN=IR,IRQ=IQ,ISL=IS,ISR=IL,ITA=IT,JAM=JM,JEY=JE,JOR=JO,JPN=JP,KAZ=KZ,KEN=KE,KGZ=KG,KHM=KH,KIR=KI,KNA=KN,KOR=KR,KWT=KW,LAO=LA,LBN=LB,LBR=LR,LBY=LY,LCA=LC,LIE=LI,LKA=LK,LSO=LS,LTU=LT,LUX=LU,LVA=LV,MAC=MO,MAF=MF,MAR=MA,MCO=MC,MDA=MD,MDG=MG,MDV=MV,MEX=MX,MHL=MH,MKD=MK,MLI=ML,MLT=MT,MMR=MM,MNE=ME,MNG=MN,MNP=MP,MOZ=MZ,MRT=MR,MSR=MS,MTQ=MQ,MUS=MU,MWI=MW,MYS=MY,MYT=YT,NAM=NA,NCL=NC,NER=NE,NFK=NF,NGA=NG,NIC=NI,NIU=NU,NLD=NL,NOR=NO,NPL=NP,NRU=NR,NZL=NZ,OMN=OM,PAK=PK,PAN=PA,PCN=PN,PER=PE,PHL=PH,PLW=PW,PNG=PG,POL=PL,PRI=PR,PRK=KP,PRT=PT,PRY=PY,PSE=PS,PYF=PF,QAT=QA,REU=RE,ROU=RO,RUS=RU,RWA=RW,SAU=SA,SDN=SD,SEN=SN,SGP=SG,SGS=GS,SHN=SH,SJM=SJ,SLB=SB,SLE=SL,SLV=SV,SMR=SM,SOM=SO,SPM=PM,SRB=RS,SSD=SS,STP=ST,SUR=SR,SVK=SK,SVN=SI,SWE=SE,SWZ=SZ,SXM=SX,SYC=SC,SYR=SY,TCA=TC,TCD=TD,TGO=TG,THA=TH,TJK=TJ,TKL=TK,TKM=TM,TLS=TL,TON=TO,TTO=TT,TUN=TN,TUR=TR,TUV=TV,TWN=TW,TZA=TZ,UGA=UG,UKR=UA,UMI=UM,URY=UY,USA=US,UZB=UZ,VAT=VA,VCT=VC,VEN=VE,VGB=VG,VIR=VI,VNM=VN,VUT=VU,WLF=WF,WSM=WS,YEM=YE,ZAF=ZA,ZMB=ZM,ZWE=ZW'.split(',').map(x => x.split('=')));
const spDn = {};
function spCountryName(code, lang) {
  const a2 = SP_A3_A2[code], lg = lang || (LANG === 'ja' ? 'ja' : 'en');
  if (!a2) return '';
  try {
    if (!spDn[lg]) spDn[lg] = new Intl.DisplayNames([lg], { type: 'region' });
    const n = spDn[lg].of(a2);
    return n && n !== a2 ? n : '';
  } catch (e) { return ''; }
}
function spCountryLabel(code) {            // 「アメリカ合衆国（USA）」。名前が分からなければ符号だけ
  const n = spCountryName(code);
  return n ? t('sp_country_fmt').replace('{name}', n).replace('{code}', code) : code;
}
// 欄に入れた文字 → 国の符号。3 文字の符号、2 文字の符号、国の名前（日本語・英語）のどれでもよい。空なら ''、分からなければ null。
// codes = SpeciesNet の一覧にある符号（まだ読めていなければ空。そのときは、3 文字ならそのまま通す）
function spCountryResolve(text, codes) {
  const s = (text || '').trim();
  if (!s) return '';
  const up = s.toUpperCase(), known = codes && codes.length ? codes : null;
  if (/^[A-Z]{3}$/.test(up) && (!known || known.includes(up))) return up;
  const pool = known || Object.keys(SP_A3_A2);
  if (/^[A-Z]{2}$/.test(up)) { const c = pool.find(x => SP_A3_A2[x] === up); if (c) return c; }
  const low = s.toLowerCase();
  for (const c of pool) for (const lg of ['ja', 'en']) { const n = spCountryName(c, lg); if (n && n.toLowerCase() === low) return c; }
  return null;
}
// 国の欄の動き: 3 文字の符号を打ち終えたら、すぐに反映する（Enter や欄の外を押すのを待たない）。国の名前は、打ち終えて Enter か欄の外で反映する。
// o.codes() = 符号の一覧、o.current() = いまの国、o.apply(code) = 反映する、o.bad() = 分からない文字だったとき、
// o.same() = いまの国と同じだったとき（横の説明を出し直す）、o.typing() = まだ決まらない文字を打っている間（横の説明を消す。前の国の説明が、違う文字の横に残らないように）
function spWireCountry(input, o) {
  const go = final => {
    const code = spCountryResolve(input.value, o.codes());
    if (code === null) { if (final) o.bad(); else if (o.typing) o.typing(); return; }
    if (code !== o.current()) { o.apply(code); return; }
    if (final) input.value = code;
    if (o.same) o.same();
  };
  input.addEventListener('input', () => { const v = input.value.trim(); if (v === '' || /^[A-Za-z]{3}$/.test(v)) go(false); else if (o.typing) o.typing(); });
  input.addEventListener('change', () => go(true));
}
function spFillCountries(datalist, codes) {
  if (datalist) datalist.innerHTML = (codes || []).map(c => `<option value="${esc(c)}" label="${esc(spCountryName(c))}">`).join('');
}

// 自動判定での対応の印（✓ 種で対応 / ✓ 属で対応 / — 対応なし / ? 未確認）
function spAiChip(level) {
  const lv = level || 'none';
  const ok = ['species', 'genus', 'family', 'order', 'class'].includes(lv);
  const tip = t('sp_ai_tip_' + (ok ? (lv === 'species' || lv === 'genus' ? lv : 'higher') : lv));
  return `<span class="sp-chip ${ok ? 'ok' : lv}" title="${esc(tip)}">${ok ? '✓' : (lv === 'unknown' ? '?' : '—')} ${esc(t('sp_ai_' + lv))}</span>`;
}

function spMethodCards() {
  const few = ((S.species && S.species.species) || []).length < 10;        // 日本の哺乳類の一覧は、種がまだ少ないときだけ出す（もう入っているプロジェクトでは要らない）
  return `<div class="sp-methods">${SP_METHODS.filter(k => k !== 'defaults' || few).map(k => `<button type="button" class="sp-method" data-m="${k}"><b>${esc(t('sr_m_' + k))}</b><span>${esc(t('sr_m_' + k + '_help'))}</span></button>`).join('')}</div>`;
}
function spWireMethods(m, opts) {
  const go = { manual: speciesManualDialog, csv: speciesCsvDialog, cand: speciesCandidatesDialog, defaults: speciesDefaultsAdd };
  m.querySelectorAll('.sp-method').forEach(b => { b.onclick = () => go[b.dataset.m](opts); });
}

// ---------- 候補の一覧（SpeciesNet が知っている種のうち、その国で候補になるもの） ----------
// box に、探す欄・絞り込み・チェックつきの一覧を描く。種リストの窓の下（国を入れると出る）と、「SpeciesNet の候補から選ぶ」の窓で使う。
//   o.hideHave    登録済みの種を出さない（種リストの窓では、上の表にあるので）
//   o.needCountry 国が空のときは、一覧の代わりに「国を入れると出ます」を出す（「国を決めずに全部出す」も選べる）
//   o.onRegister  あれば、一覧の上に「選んだ k 種を登録」のボタンを出す
//   o.onCount(k)  選んだ数が変わったとき、o.onData(data) = 候補を読んだとき
// 返り値: load(country) = 読み直す（country を渡さなければ、プロジェクトの国）、picked = 選んだ種、stop() = モデルの取得を待つのをやめる
function spCandidatesPane(box, o) {
  o = o || {};
  const picked = new Set();
  const f = { q: '', mam: true, bird: false, other: false, higher: false, all: false };      // 絞り込みは、国を変えても持ち越す
  let data = null, poll = null;
  const bq = sel => box.querySelector(sel);
  const alive = () => document.body.contains(box);
  const stop = () => { if (poll) { clearInterval(poll); poll = null; } };
  const groupOf = x => x.cls === 'mammalia' ? 'mam' : (x.cls === 'aves' ? 'bird' : 'other');
  const visible = () => {
    const text = f.q.trim().toLowerCase();
    return data.rows.filter(x => !(o.hideHave && x.have) && f[groupOf(x)] && (f.higher || x.rank === 'species') && (!text || x.name.toLowerCase().includes(text) || x.sci.toLowerCase().includes(text) || x.order.includes(text) || x.family.includes(text)));
  };
  const count = () => {
    const c = bq('#skCount'), b = bq('#skReg');
    if (c) c.textContent = t('sk_count').replace('{n}', visible().length).replace('{k}', picked.size);
    if (b) { b.textContent = t('sk_add').replace('{k}', picked.size); b.disabled = !picked.size; }
    if (o.onCount) o.onCount(picked.size);
  };
  const list = () => {
    const rows = visible(), el = bq('#skList');
    if (!el) return;
    el.innerHTML = rows.length ? `<table class="list"><tr><th></th><th>${esc(t('sk_col_name'))}</th><th>${esc(t('sp_sci'))}</th><th>${esc(t('sk_col_group'))}</th></tr>${rows.map(x => `<tr class="${x.have ? 'off' : ''}"><td><input type="checkbox" data-v="${esc(x.value)}" ${x.have && x.on ? 'checked disabled' : (picked.has(x.value) ? 'checked' : '')}></td><td>${esc(x.name)}${x.rank !== 'species' ? ` <span class="info">${esc(t('sp_ai_' + x.rank))}</span>` : ''}${x.have ? ` <span class="info">${esc(t(x.on ? 'sk_have' : 'sk_have_off'))}</span>` : ''}</td><td><i>${esc(x.sci)}</i></td><td class="info">${esc(x.order)} / ${esc(x.family)}</td></tr>`).join('')}</table>` : `<div class="note sk-nothing">${esc(t('sk_nothing'))}</div>`;
    el.querySelectorAll('input[data-v]').forEach(c => { c.onchange = () => { if (c.checked) picked.add(c.dataset.v); else picked.delete(c.dataset.v); count(); }; });
    count();
  };
  const show = () => {
    if (!alive()) return;
    if (o.onData) o.onData(data);
    if (!data.available) {                     // SpeciesNet のモデルをまだ取ってきていない: 取ってくるボタンを出す（終わるまで、ときどき見に行く）
      box.innerHTML = `<div class="sp-empty">${esc(t(data.env_speciesnet ? 'sk_none_avail' : 'sk_no_env'))}</div>${data.env_speciesnet ? `<button type="button" id="skFetch">${esc(t('sk_fetch'))}</button> <span class="info" id="skFetchInfo"></span>` : ''}`;
      const fb = bq('#skFetch');
      if (fb) fb.onclick = async () => {
        try {
          await api('/api/species/model', {});
          fb.disabled = true; bq('#skFetchInfo').textContent = t('sk_fetching');
          stop();
          poll = setInterval(async () => { if (!alive()) { stop(); return; } try { const r = await api('/api/species/candidates'); if (r.available) { stop(); data = r; show(); } } catch (e) { /* 次の回に */ } }, 4000);
        } catch (e) { toast(e.message, 'error'); }
      };
      count();
      return;
    }
    if (o.needCountry && !data.country && !f.all) {      // 国が空: 2,000 種を黙って並べない。国を入れるように言う
      box.innerHTML = `<div class="sp-empty">${esc(t('sp_cand_need'))} <button type="button" class="small" id="skShowAll">${esc(t('sp_cand_all'))}</button></div>`;
      bq('#skShowAll').onclick = () => { f.all = true; show(); };
      count();
      return;
    }
    const chk = (id, key, label) => `<label class="chk"><input type="checkbox" id="${id}" ${f[key] ? 'checked' : ''}> ${esc(t(label))}</label>`;
    box.innerHTML = `<div class="sk-tools"><input type="text" id="skQ" placeholder="${esc(t('sk_q'))}" spellcheck="false" value="${esc(f.q)}">${chk('skMam', 'mam', 'sk_mam')}${chk('skBird', 'bird', 'sk_bird')}${chk('skOther', 'other', 'sk_other')}${chk('skHigher', 'higher', 'sk_higher')}</div>
      <div class="sk-bar"><span id="skCount"></span><button type="button" class="small" id="skAll">${esc(t('sk_all'))}</button><button type="button" class="small" id="skClear">${esc(t('sk_clear'))}</button>${o.onRegister ? `<button type="button" class="primary" id="skReg" disabled></button>` : ''}</div>
      <div class="sp-tablebox" id="skList"></div>`;
    bq('#skQ').addEventListener('input', () => { f.q = bq('#skQ').value; list(); });
    [['#skMam', 'mam'], ['#skBird', 'bird'], ['#skOther', 'other'], ['#skHigher', 'higher']].forEach(([sel, key]) => bq(sel).addEventListener('change', () => { f[key] = bq(sel).checked; list(); }));
    bq('#skAll').onclick = () => { visible().forEach(x => { if (!(x.have && x.on)) picked.add(x.value); }); list(); };
    bq('#skClear').onclick = () => { picked.clear(); list(); };
    if (o.onRegister) bq('#skReg').onclick = o.onRegister;
    list();
  };
  const load = async country => {
    try {
      data = await api('/api/species/candidates' + (country == null ? '' : '?country=' + encodeURIComponent(country)));
      const ok = new Set(data.rows.filter(x => !(x.have && x.on)).map(x => x.value));       // 国を変えたら、その国の候補にないものは、選んだ中から外す
      Array.from(picked).forEach(v => { if (!ok.has(v)) picked.delete(v); });
      show();
    } catch (e) { toast(e.message, 'error'); }
    return data;
  };
  return { load, picked, stop, data: () => data };
}

// ---------- 種リストの窓 ----------
async function speciesDialog(opts) {
  opts = opts || {};
  let sp, st;
  try { [sp, st] = await Promise.all([api('/api/species'), api('/api/species/status')]); } catch (e) { toast(e.message, 'error'); return; }
  S.species = sp; rebuildSpMap();
  const clone = x => JSON.parse(JSON.stringify(x));
  let rows = clone(sp.species), adv = false, codes = [], pane = null;
  const ja = LANG === 'ja';
  const self = () => speciesDialog(opts);
  const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sp_title'))}</h3>
    <div class="note">${esc(t('sp_help'))}</div>
    <div class="form-row"><label>${esc(t('sp_country'))}</label><span class="sp-country"><input type="text" id="spCountry" value="${esc(st.country)}" spellcheck="false" list="spCountries" placeholder="${esc(t('sp_country_ph'))}"><datalist id="spCountries"></datalist> <span class="info" id="spCountryInfo"></span></span><div class="help">${esc(t('sp_country_help'))}</div></div>
    <div id="spReg"></div>
    <div class="sp-section" id="spCandHead"></div>
    <div id="spCand"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="primary">${esc(t('save'))}</button></div></div>`);
  const q = sel => m.querySelector(sel);
  const done = () => { if (pane) pane.stop(); if (opts.onClose) opts.onClose(); else closeModal(); };
  const read = () => { m.querySelectorAll('#spReg tr[data-i]').forEach(tr => { const r = rows[parseInt(tr.dataset.i)]; tr.querySelectorAll('[data-k]').forEach(inp => { r[inp.dataset.k] = inp.type === 'checkbox' ? (inp.checked ? 1 : 0) : inp.value; }); }); };
  const save = async () => { read(); S.species = await api('/api/species', { rows }); rebuildSpMap(); rows = clone(S.species.species); };      // 保存すると、新しい行に code と自動判定の対応が付く
  const countryInfo = () => {
    const el = q('#spCountryInfo'); if (!el) return;
    el.innerHTML = !st.country_known ? `<span class="sp-warn">${esc(t('sp_country_bad'))}</span>` : (st.country ? esc(st.n_candidates != null ? t('sp_country_n').replace('{c}', spCountryLabel(st.country)).replace('{n}', st.n_candidates) : spCountryLabel(st.country)) : esc(t('sp_country_none')));
    q('#spCandHead').innerHTML = `<b>${esc(st.country ? t('sp_cand_title').replace('{c}', spCountryLabel(st.country)) : t('sp_cand_title_all'))}</b> <span class="info">${esc(t('sp_cand_help'))}</span>`;
  };
  const renderReg = () => {
    const showJa = ja || rows.some(r => r.name_ja);
    const n = rows.length, on = rows.filter(r => r.enabled).length;
    const sum = t('sp_sum').replace('{n}', n).replace('{m}', on).replace('{a}', st.ai.length) + (st.no_ai.length ? t('sp_sum_noai').replace('{k}', st.no_ai.length) : '');
    const head = `<tr><th>${esc(t('sp_use'))}</th>${showJa ? `<th>${esc(t('sp_ja'))}</th>` : ''}<th>${esc(t(showJa ? 'sp_en' : 'sp_name'))}</th><th>${esc(t('sp_sci'))}</th><th>${esc(t('sp_csv'))}</th><th>${esc(t('sp_key'))}</th><th>${esc(t('sp_ai'))}</th>${adv ? `<th>${esc(t('sp_rank'))}</th><th>${esc(t('sp_nn'))}</th><th>${esc(t('sp_sn'))}</th>` : ''}<th></th></tr>`;
    const body = rows.map((r, i) => `<tr class="${r.enabled ? '' : 'off'}" data-i="${i}"><td><input type="checkbox" data-k="enabled" ${r.enabled ? 'checked' : ''}></td>${showJa ? `<td><input data-k="name_ja" value="${esc(r.name_ja)}"></td>` : ''}<td><input data-k="name_en" value="${esc(r.name_en)}"></td><td><input data-k="name_sci" value="${esc(r.name_sci)}" spellcheck="false"></td><td><input data-k="csv_label" value="${esc(r.csv_label)}" spellcheck="false"></td><td><input data-k="key" value="${esc(r.key)}" maxlength="1" style="width:34px"></td><td class="sp-ai">${r.code ? spAiChip(st.status[r.code]) : ''}</td>${adv ? `<td><input data-k="rank" value="${esc(r.rank)}" style="width:40px"></td><td><input type="checkbox" data-k="non_native" ${r.non_native ? 'checked' : ''}></td><td><input data-k="speciesnet" value="${esc(r.speciesnet)}" spellcheck="false"></td>` : ''}<td><button type="button" class="small del" title="${esc(t('sp_del'))}">✕</button></td></tr>`).join('');
    m.querySelector('.sp-wrap').classList.toggle('sp-noreg', !n);
    q('#spReg').innerHTML = `<div class="sp-section"><b>${esc(t('sp_reg_title'))}</b> <span class="info">${esc(sum)}</span></div>
      <div class="btn-row sp-actions"><button type="button" class="${n ? '' : 'primary'}" id="spRegister">${esc(t('sp_register'))}</button>${n ? `<button type="button" class="small" id="spAllOn">${esc(t('sp_all_on'))}</button><button type="button" class="small" id="spAllOff">${esc(t('sp_all_off'))}</button><button type="button" class="small" id="spAdv">${esc(t(adv ? 'sp_adv_off' : 'sp_adv'))}</button>` : ''}</div>
      ${n ? `<div class="note">${esc(t('sp_names_help'))}</div><div class="sp-tablebox"><table class="list sp-table">${head}${body}</table></div>` : `<div class="sp-empty">${esc(t('sp_empty'))}</div>`}`;
    q('#spRegister').onclick = async () => { try { await save(); if (pane) pane.stop(); speciesRegisterDialog({ back: self }); } catch (e) { toast(e.message, 'error'); } };      // 表で直したぶんは、先に保存する
    if (!n) return;
    m.querySelectorAll('#spReg button.del').forEach(b => { b.onclick = () => { read(); rows.splice(parseInt(b.closest('tr').dataset.i), 1); renderReg(); }; });
    q('#spAllOn').onclick = () => { read(); rows.forEach(r => { r.enabled = 1; }); renderReg(); };
    q('#spAllOff').onclick = () => { read(); rows.forEach(r => { r.enabled = 0; }); renderReg(); };
    q('#spAdv').onclick = () => { read(); adv = !adv; renderReg(); };
  };
  // 候補から登録: 表で直したぶんを保存してから登録し、表と候補の一覧を読み直す（登録した種は、上の表に移る）
  const registerPicked = async () => {
    if (!pane.picked.size) return 0;
    await save();
    const r = await api('/api/species/add_many', { values: Array.from(pane.picked) });
    pane.picked.clear();
    S.species = { species: r.species, fixed: r.fixed }; rebuildSpMap(); rows = clone(r.species);
    return r.added + r.existed;
  };
  const afterChange = async () => { st = await api('/api/species/status'); renderReg(); countryInfo(); spRefreshMain(); };
  pane = spCandidatesPane(q('#spCand'), {
    hideHave: true, needCountry: true,
    onData: d => { codes = d.countries || []; spFillCountries(q('#spCountries'), codes); },
    onRegister: async () => { try { const k = await registerPicked(); await afterChange(); await pane.load(st.country); if (k) toast(t('sr_added').replace('{n}', k)); } catch (e) { toast(e.message, 'error'); } },
  });
  spWireCountry(q('#spCountry'), {
    codes: () => codes, current: () => st.country, same: () => countryInfo(), typing: () => { q('#spCountryInfo').textContent = ''; },
    bad: () => { q('#spCountryInfo').innerHTML = `<span class="sp-warn">${esc(t('sp_country_bad'))}</span>`; },
    apply: async code => {                     // 国を入れた・変えた: プロジェクトの国（自動判定の geofence）に保存し、下の候補を、その国のものにする
      try {
        const r = await api('/api/project/settings', { country: code });
        if (S.project) S.project.settings = r.settings;
        st = await api('/api/species/status');
        if (q('#spCountry') && document.activeElement !== q('#spCountry')) q('#spCountry').value = code;
        countryInfo();
        await pane.load(code);
      } catch (e) { toast(e.message, 'error'); }
    },
  });
  q('#mCancel').onclick = done;
  q('#mOk').onclick = async () => {            // 保存: 表で直したぶんと、候補でチェックを付けたぶんを、どちらも入れる
    try { if (pane.picked.size) await registerPicked(); else await save(); spRefreshMain(); done(); } catch (e) { toast(e.message, 'error'); }
  };
  renderReg(); countryInfo();
  pane.load(st.country);
}

// ---------- 種を登録: 登録のしかたを選ぶ ----------
function speciesRegisterDialog(opts) {
  opts = opts || {};
  const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sr_title'))}</h3><div class="note">${esc(t('sr_help'))}</div>
    ${spMethodCards()}
    <div class="modal-actions"><button id="mCancel">${esc(t(opts.back ? 'sr_back' : 'close'))}</button></div></div>`);
  m.querySelector('#mCancel').onclick = () => { if (opts.back) opts.back(); else closeModal(); };
  spWireMethods(m, opts);
}
// 登録が済んだあと: 種リストを読み直して、戻る先（種リストの窓）へ。なければ窓を閉じる
async function speciesAfterRegister(opts, msg) {
  try { await speciesReload(); } catch (e) { /* 読み直せなくても、登録は済んでいる */ }
  if (msg) toast(msg);
  if (opts && opts.back) opts.back(); else closeModal();
}

// ---- 1 種ずつ入れる ----
function speciesManualDialog(opts) {
  opts = opts || {};
  const ja = LANG === 'ja';
  const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sr_m_manual'))}</h3><div class="note">${esc(t('as_help'))}</div>
    ${ja ? `<div class="form-row"><label>${esc(t('sp_ja'))}</label><input type="text" id="asJa"></div>` : ''}
    <div class="form-row"><label>${esc(t(ja ? 'sp_en' : 'sp_name'))}</label><input type="text" id="asEn"></div>
    <div class="form-row"><label>${esc(t('sp_sci'))}</label><input type="text" id="asSci" spellcheck="false" placeholder="${esc(t('as_sci_ph'))}"><div class="help" id="asAi"></div></div>
    <div class="form-row"><label>${esc(t('sp_csv'))}</label><input type="text" id="asCsv" spellcheck="false" placeholder="${esc(t('as_csv_ph'))}"><div class="help">${esc(t('as_csv_help'))}</div></div>
    <div class="form-row"><label>${esc(t('sp_key'))}</label><input type="text" id="asKey" maxlength="1" style="width:40px"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('sr_back'))}</button><button id="mOk" class="primary">${esc(t('as_ok'))}</button></div></div>`);
  const q = sel => m.querySelector(sel);
  q('#mCancel').onclick = () => speciesRegisterDialog(opts);
  let timer = null, seq = 0;
  q('#asSci').addEventListener('input', () => {            // 学名を打つと、自動判定（SpeciesNet）での対応を出す
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const sci = q('#asSci') ? q('#asSci').value.trim() : '', me = ++seq;
      if (!sci) { if (q('#asAi')) q('#asAi').textContent = ''; return; }
      try {
        const r = await api('/api/species/suggest?sci=' + encodeURIComponent(sci));
        if (me !== seq || !q('#asAi')) return;
        const lv = r.level || 'none', kind = lv === 'species' || lv === 'genus' || lv === 'none' ? lv : 'higher';
        q('#asAi').textContent = r.available ? t('as_ai_' + kind).replace('{v}', r.value || '') : '';
        q('#asAi').className = 'help' + (r.available && lv === 'none' ? ' sp-warn' : '');
      } catch (e) { /* 出せなくても登録はできる */ }
    }, 300);
  });
  const ok = async () => {
    const row = { name_ja: ja ? q('#asJa').value.trim() : '', name_en: q('#asEn').value.trim(), name_sci: q('#asSci').value.trim(), csv_label: q('#asCsv').value.trim(), key: q('#asKey').value.trim(), rank: '種', enabled: 1 };
    if (!row.name_ja && !row.name_en && !row.name_sci) { toast(t('as_need_name'), 'error'); return; }
    try {
      const r = await api('/api/species/add', row);
      const existed = (S.species.species || []).some(s => s.code === r.added.code);
      S.species = { species: r.species, fixed: r.fixed }; rebuildSpMap();
      if (existed) toast(t('as_exists'));
      if (opts.selectAfter) {                          // 判定の欄の「＋ 登録」から: いま開いている動画で、その種を選ぶ
        closeModal();
        if (S.cur && !S.sel.includes(r.added.code)) toggleSpecies(r.added.code); else { renderSpeciesPanel(); renderFieldsPanel(); }
        renderFilters();
        return;
      }
      speciesAfterRegister(opts, existed ? '' : t('sr_added').replace('{n}', 1));
    } catch (e) { toast(e.message, 'error'); }
  };
  q('#mOk').onclick = ok;
  m.querySelectorAll('input').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); ok(); } }));
  (q('#asJa') || q('#asEn')).focus();
}

// ---- CSV からまとめて登録: ひな形を書き出す → 種を書き込む → 読み込む（読む前に、何が変わるかを見せる） ----
function speciesCsvDialog(opts) {
  opts = opts || {};
  const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sr_m_csv'))}</h3>
    <ol class="sp-steps">
      <li><b>${esc(t('sc_1'))}</b><div class="note">${esc(t('sc_1_help'))}</div><button type="button" id="scTpl">${esc(t('sc_1_btn'))}</button> <span class="info" id="scTplStatus"></span></li>
      <li><b>${esc(t('sc_2'))}</b><div class="note">${esc(t('sc_2_help'))}</div></li>
      <li><b>${esc(t('sc_3'))}</b><div class="note">${esc(t('sc_3_help'))}</div><button type="button" id="scPick">${esc(t('sc_3_btn'))}</button> <span class="info" id="scFile"></span><div id="scPlan"></div></li>
    </ol>
    <div class="modal-actions"><button id="mCancel">${esc(t('sr_back'))}</button><button id="mOk" class="primary" disabled>${esc(t('sc_apply'))}</button></div></div>`);
  const q = sel => m.querySelector(sel);
  let path = '';
  q('#mCancel').onclick = () => speciesRegisterDialog(opts);
  q('#scTpl').onclick = async () => {
    const p = await pickPath('savecsv', `${S.project.dir}/exports`, 'species_template.csv');
    if (!p) return;
    try { const r = await api('/api/species/template', { path: p }); if (q('#scTplStatus')) q('#scTplStatus').textContent = `${t('sc_1_done')}: ${r.path}`; } catch (e) { toast(e.message, 'error'); }
  };
  q('#scPick').onclick = async () => {
    const p = await pickPath('csv', `${S.project.dir}/exports`);
    if (!p) return;
    try {
      const r = await api('/api/species/import/preview', { path: p });
      if (!q('#scPlan')) return;
      path = p;
      q('#scFile').textContent = p;
      const lines = [t('sc_plan').replace('{new}', r.new).replace('{update}', r.update).replace('{same}', r.same)];
      if (r.fixed) lines.push(t('sc_skip_fixed').replace('{n}', r.fixed));
      if (r.dup) lines.push(t('sc_skip_dup').replace('{n}', r.dup));
      if ((r.no_name || []).length) lines.push(t('sc_no_name').replace('{lines}', r.no_name.join(', ')));
      if ((r.unknown || []).length) lines.push(t('sc_unknown').replace('{cols}', r.unknown.join(', ')));
      const show = (r.plan || []).filter(x => x.action === 'new' || x.action === 'update').slice(0, 60);
      const table = show.length ? `<div class="sp-tablebox small"><table class="list"><tr><th>${esc(t('sc_col_line'))}</th><th>${esc(t('sp_name'))}</th><th>${esc(t('sp_sci'))}</th><th>${esc(t('sc_col_do'))}</th></tr>${show.map(x => `<tr><td>${x.line}</td><td>${esc(x.name)}</td><td>${esc(x.sci)}</td><td>${esc(t('sc_a_' + x.action))}${x.changes && x.changes.length ? ` <span class="info">${esc(x.changes.map(c => t('sc_f_' + c)).join(t('ex_dp_sep')))}</span>` : ''}</td></tr>`).join('')}</table></div>` : '';
      q('#scPlan').innerHTML = `<div class="sp-plan ${r.new + r.update ? 'ok' : ''}">${lines.map(esc).join('<br>')}</div>${table}`;
      q('#mOk').disabled = !(r.new + r.update);
    } catch (e) { toast(e.message, 'error'); }
  };
  q('#mOk').onclick = async () => {
    if (!path) return;
    try {
      const r = await api('/api/species/import', { path });
      speciesAfterRegister(opts, t('sc_done').replace('{new}', r.new).replace('{update}', r.update));
    } catch (e) { toast(e.message, 'error'); }
  };
}

// ---- SpeciesNet の候補から選ぶ（右の欄の「＋ 登録」などから。種リストの窓では、同じ一覧が窓の下に出る） ----
async function speciesCandidatesDialog(opts) {
  opts = opts || {};
  const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sr_m_cand'))}</h3><div class="note">${esc(t('sk_help'))}</div>
    <div class="form-row"><label>${esc(t('sp_country'))}</label><span class="sp-country"><input type="text" id="skCountry" spellcheck="false" list="skCountries" placeholder="${esc(t('sp_country_ph'))}"><datalist id="skCountries"></datalist> <span class="info" id="skCountryInfo"></span></span><div class="help">${esc(t('sp_country_help'))}</div></div>
    <div id="skBody"><div class="note">${esc(t('loading'))}</div></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('sr_back'))}</button><button id="mOk" class="primary" disabled>${esc(t('sk_add').replace('{k}', 0))}</button></div></div>`);
  const q = sel => m.querySelector(sel);
  let cur = null, codes = [];
  const info = d => { const el = q('#skCountryInfo'); if (el) el.innerHTML = !d.country_known ? `<span class="sp-warn">${esc(t('sp_country_bad'))}</span>` : esc(d.country ? spCountryLabel(d.country) : t('sp_country_none')); };
  const pane = spCandidatesPane(q('#skBody'), {
    onCount: k => { const b = q('#mOk'); if (b) { b.textContent = t('sk_add').replace('{k}', k); b.disabled = !k; } },
    onData: d => { codes = d.countries || []; spFillCountries(q('#skCountries'), codes); if (cur === null) { cur = d.country || ''; q('#skCountry').value = cur; } info(d); },
  });
  q('#mCancel').onclick = () => { pane.stop(); speciesRegisterDialog(opts); };
  spWireCountry(q('#skCountry'), {
    codes: () => codes, current: () => cur, same: () => { const d = pane.data(); if (d) info(d); }, typing: () => { q('#skCountryInfo').textContent = ''; },
    bad: () => { q('#skCountryInfo').innerHTML = `<span class="sp-warn">${esc(t('sp_country_bad'))}</span>`; },
    apply: async code => {                     // 国を変えたら、プロジェクトの国（自動判定の geofence）も同じにする
      cur = code;
      if (document.activeElement !== q('#skCountry')) q('#skCountry').value = code;
      await pane.load(code);
      try { const r = await api('/api/project/settings', { country: code }); if (S.project) S.project.settings = r.settings; } catch (e) { toast(e.message, 'error'); }
    },
  });
  q('#mOk').onclick = async () => {
    if (!pane.picked.size) return;
    try {
      const r = await api('/api/species/add_many', { values: Array.from(pane.picked) });
      pane.stop();
      speciesAfterRegister(opts, t('sr_added').replace('{n}', r.added) + (r.existed ? t('sr_added_have').replace('{k}', r.existed) : ''));
    } catch (e) { toast(e.message, 'error'); }
  };
  pane.load(null);
}

// ---- 日本の哺乳類の一覧を入れる ----
async function speciesDefaultsAdd(opts) {
  try {
    const r = await api('/api/species/add_many', { defaults: true });
    speciesAfterRegister(opts, r.added ? t('sr_added').replace('{n}', r.added) : t('sr_defaults_none'));
  } catch (e) { toast(e.message, 'error'); }
}

// ---------- 自動判定の窓に出す、種リストの様子（どの種が付くか。1 つもなければ、始めさせない） ----------
// el = 入れる場所。返り値 = 自動判定で付けられる種の数（分からなければ null）
async function speciesBlockForDetect(el, onOpen) {
  let st;
  try { st = await api('/api/species/status'); } catch (e) { return null; }
  if (!document.body.contains(el)) return null;
  const names = cs => { const xs = cs.map(c => spLabel(c)); return xs.slice(0, 14).join(t('ex_dp_sep')) + (xs.length > 14 ? ` ${t('dt_sp_more').replace('{k}', xs.length - 14)}` : ''); };
  const n = st.ai.length;
  el.className = 'dt-species not-lite ' + (n ? 'ok' : 'ng');
  el.innerHTML = `<div class="dt-sp-head"><b>${esc(t('dt_sp_title'))}</b>: ${esc(t('dt_sp_n').replace('{n}', n))} <button type="button" class="small" id="dtSpOpen">${esc(t('dt_sp_open'))}</button></div>`
    + (n ? `<div>${esc(names(st.ai))}</div><div class="info">${esc(t('dt_sp_note'))}</div>` : `<div class="sp-warn">${esc(t('dt_sp_zero'))}</div>`)
    + (st.no_ai.length ? `<div class="info">${esc(t('dt_sp_noai'))}: ${esc(names(st.no_ai))}</div>` : '')
    + `<div class="info">${esc(t('sp_country'))}: ${esc(st.country ? spCountryLabel(st.country) : t('dt_sp_country_none'))}${st.country_known ? '' : ` — ${esc(t('sp_country_bad'))}`}</div>`;
  const b = el.querySelector('#dtSpOpen');
  if (b) b.onclick = onOpen;
  return n;
}

// ---------- 種のショートカットキー（確認事項 154 回目）: 右の欄の「キー設定」から開く ----------
// 種の行を選んで（押すか、矢印キー）、付けたいキーを押す。付けると、次の種に進む。Backspace で外す。Enter で保存。保存は POST /api/species { keys: { 種のコード: キー } }
// SP_KEYS_RESERVED = 判定の画面（app.js の onKey）が、種のキーより先に使うキー。種に付けても届かないので、付けさせない
//                    （サーバーの species.KEYS_RESERVED と同じ集まり。tests/test_species_keys.py が見る）
// SP_KEYS_SHARED   = 種に付いていなければ、ほかの操作に使うキー。種に付けられる（付けている間、その操作はキーではできない）
const SP_KEYS_RESERVED = { ' ': 'play', ',': 'frame', '.': 'frame', '<': 'frame', '>': 'frame', '[': 'speed', ']': 'speed' };
const SP_KEYS_SHARED = { F: 'fav', B: 'enh', L: 'loop', '-': 'enh', '+': 'enh', '=': 'enh' };
function spKeyChar(e) {                    // 押したキー → 種のキーにする 1 文字（英字は大文字）。文字のキーでなければ ''
  let k = e.key || '';
  if (k === 'Process' || k === 'Unidentified') {          // 日本語入力が入っているとき: 押した場所（code）から、英字・数字を取る
    const c = e.code || '';
    k = /^Key[A-Z]$/.test(c) ? c.slice(3) : (/^(Digit|Numpad)\d$/.test(c) ? c.slice(-1) : '');
  }
  if (k.length !== 1) return '';
  const u = k.toUpperCase();
  return u.length === 1 ? u : k;
}
function speciesKeysDialog() {
  const all = (S.species && S.species.species) || [], fixed = (S.species && S.species.fixed) || [];
  const rows = all.filter(s => s.enabled);                // 右の欄に並んでいる種（同じ順）
  const keys = {};                         // 種のコード → キー。「使う」でない種のぶんも持つ（同じキーを、そこから移すため）
  all.forEach(s => { keys[s.code] = (s.key || '').toUpperCase(); });
  const fixedBy = {};
  fixed.forEach(s => { if (s.key) fixedBy[s.key.toUpperCase()] = s; });
  const fmt = (key, o) => t(key).replace(/\{(\w+)\}/g, (_, k) => (o[k] != null ? o[k] : ''));
  const nameOf = code => { const s = all.find(x => x.code === code); return s ? spName(s) : code; };
  const rowHtml = (s, fx) => `<div class="kd-row${fx ? ' fixed' : ''}"${fx ? '' : ` tabindex="0" data-code="${esc(s.code)}"`}><span class="kd-key">${fx ? esc(s.key || '') : ''}</span><span class="kd-name">${esc(spName(s))}</span></div>`;
  const m = showModal(`<div class="kd-wrap"><h3>${esc(t('kd_title'))}</h3><div class="note">${esc(t('kd_help'))}</div>
    ${rows.length ? `<div class="kd-tools"><button type="button" class="small" id="kdAuto" title="${esc(t('kd_auto_tip'))}">${esc(t('kd_auto'))}</button><button type="button" class="small" id="kdClear">${esc(t('kd_clear'))}</button></div>
    <div class="kd-list" id="kdList">${rows.map(s => rowHtml(s, false)).join('')}</div>` : `<div class="sp-empty">${esc(t('kd_none'))}</div>`}
    <div class="kd-msg" id="kdMsg"></div>
    <div class="kd-sec">${esc(t('kd_fixed'))}</div><div class="kd-list fixed">${fixed.map(s => rowHtml(s, true)).join('')}</div>
    <div class="note">${esc(t('kd_used'))}</div>
    <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="primary">${esc(t('save'))}</button></div></div>`);
  const q = sel => m.querySelector(sel);
  const els = Array.from(m.querySelectorAll('.kd-row[data-code]'));
  let cur = 0;                             // いま選んでいる行（ボタンを押したあと、ここへ戻す）
  const msg = text => { q('#kdMsg').textContent = text || ''; };
  const paint = () => els.forEach(el => { const k = keys[el.dataset.code] || '', box = el.querySelector('.kd-key'); box.textContent = k; box.classList.toggle('none', !k); });
  const focusRow = i => { if (!els.length) return; cur = Math.max(0, Math.min(els.length - 1, i)); els[cur].focus(); };
  const assign = (code, ch) => {
    if (SP_KEYS_RESERVED[ch]) { msg(fmt('kd_msg_reserved', { k: ch === ' ' ? 'Space' : ch, what: t('kd_w_' + SP_KEYS_RESERVED[ch]) })); return false; }
    if (fixedBy[ch]) { msg(fmt('kd_msg_fixed', { k: ch, name: spName(fixedBy[ch]) })); return false; }
    const from = all.find(s => s.code !== code && keys[s.code] === ch);       // 同じキーは、前の種から外して移す
    if (from) keys[from.code] = '';
    keys[code] = ch;
    msg([from ? fmt('kd_msg_moved', { k: ch, from: spName(from), to: nameOf(code) }) : '', SP_KEYS_SHARED[ch] ? fmt('kd_msg_shared', { k: ch, what: t('kd_w_' + SP_KEYS_SHARED[ch]) }) : ''].filter(Boolean).join(' '));
    paint();
    return true;
  };
  const save = async () => {
    const changed = {};
    all.forEach(s => { if ((s.key || '').toUpperCase() !== keys[s.code]) changed[s.code] = keys[s.code]; });
    try {
      if (Object.keys(changed).length) { S.species = await api('/api/species', { keys: changed }); spRefreshMain(); toast(t('kd_saved')); }
      closeModal();
    } catch (e) { toast(e.message, 'error'); }
  };
  els.forEach((el, i) => { el.addEventListener('focus', () => { cur = i; }); });
  if (els.length) {
    q('#kdList').addEventListener('keydown', e => {
      const el = e.target && e.target.closest ? e.target.closest('.kd-row[data-code]') : null;
      if (!el || e.ctrlKey || e.metaKey || e.altKey) return;
      const i = els.indexOf(el), code = el.dataset.code, k = e.key;
      const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 2, ArrowUp: -2 }[k];        // 2 列に並んでいる（右の欄と同じ並び）
      if (step) { e.preventDefault(); if (i + step >= 0 && i + step < els.length) focusRow(i + step); return; }
      if (k === 'Backspace' || k === 'Delete') { e.preventDefault(); keys[code] = ''; msg(''); paint(); return; }
      if (k === 'Enter') { e.preventDefault(); save(); return; }
      const ch = spKeyChar(e);
      if (!ch) return;                     // Tab、F1 など: そのまま
      e.preventDefault();
      if (assign(code, ch) && i + 1 < els.length) focusRow(i + 1);
    });
    q('#kdAuto').onclick = () => {          // キーのない種に、上から順に、空いている数字を付ける
      const free = '1234567890'.split('').filter(d => !rows.some(s => keys[s.code] === d));
      let n = 0;
      rows.forEach(s => {
        if (keys[s.code] || !free.length) return;
        const d = free.shift();
        all.forEach(o => { if (keys[o.code] === d) keys[o.code] = ''; });      // 「使う」でない種が持っていた数字は、そこから外す
        keys[s.code] = d; n++;
      });
      msg(n ? fmt('kd_auto_done', { n }) : t('kd_auto_none'));
      paint(); focusRow(cur);
    };
    q('#kdClear').onclick = () => { rows.forEach(s => { keys[s.code] = ''; }); msg(''); paint(); focusRow(cur); };
  }
  q('#mCancel').onclick = closeModal;
  q('#mOk').onclick = save;
  paint();
  focusRow(0);
}
