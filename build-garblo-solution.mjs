import { readFile, writeFile } from 'node:fs/promises';

const architecture = await readFile('output/garblo-architecture.html', 'utf8');
const architectureBase64 = Buffer.from(architecture, 'utf8').toString('base64');
const databaseRelation = await readFile('output/garblo-database-relation.html', 'utf8');
const databaseRelationBase64 = Buffer.from(databaseRelation, 'utf8').toString('base64');

const fields = (items) => items.map(([name, type, key = '', description = '']) => `
  <li><code>${name}</code><span>${type}</span>${key ? `<em>${key}</em>` : ''}${description ? `<small>${description}</small>` : ''}</li>`).join('');

const table = (name, description, items) => `
  <article class="table-card">
    <header><h3>${name}</h3><p>${description}</p></header>
    <ul>${fields(items)}</ul>
  </article>`;

const tables = [
  table('users', 'Hồ sơ, gu và quyền Social P2', [
    ['id', 'UUID', 'PK', 'Mã định danh duy nhất của người dùng.'], ['email', 'CITEXT', 'UNIQUE', 'Email đăng nhập, không phân biệt chữ hoa/thường.'], ['display_name', 'TEXT', '', 'Tên hiển thị trong ứng dụng.'], ['style_profile', 'JSONB', '', 'Thông tin gu mặc và sở thích được suy ra hoặc tự khai báo.'], ['social_opt_in', 'BOOLEAN', '', 'Cho biết người dùng có đồng ý dùng tính năng kết nối P2 hay không.'], ['created_at', 'TIMESTAMPTZ', '', 'Thời điểm tạo tài khoản.']
  ]),
  table('consent_records', 'Dấu vết đồng ý và rút đồng ý', [
    ['id', 'UUID', 'PK', 'Mã của lần ghi nhận đồng ý.'], ['user_id', 'UUID', 'FK → users', 'Người đã đưa ra lựa chọn.'], ['purpose', 'TEXT', '', 'Mục đích xử lý dữ liệu mà người dùng đã đồng ý.'], ['policy_version', 'TEXT', '', 'Phiên bản chính sách áp dụng tại thời điểm đồng ý.'], ['granted_at', 'TIMESTAMPTZ', '', 'Thời điểm người dùng đồng ý.'], ['revoked_at', 'TIMESTAMPTZ nullable', '', 'Thời điểm rút lại đồng ý, nếu có.']
  ]),
  table('media_assets', 'Thông tin mô tả ảnh lưu trong RustFS', [
    ['id', 'UUID', 'PK', 'Mã định danh của một ảnh hoặc asset.'], ['user_id', 'UUID', 'FK → users', 'Chủ sở hữu của asset.'], ['object_key', 'TEXT', 'RustFS key', 'Đường dẫn object của tệp ảnh trong RustFS.'], ['asset_type', 'TEXT', '', 'Loại ảnh: quần áo, dáng, nền, ảnh tham khảo hoặc ảnh tạo ra.'], ['mime_type', 'TEXT', '', 'Định dạng tệp, ví dụ image/jpeg hoặc image/png.'], ['checksum', 'TEXT', '', 'Dấu vân tay tệp để kiểm tra trùng lặp và toàn vẹn.'], ['metadata', 'JSONB', '', 'Thông tin trích xuất từ ảnh, như màu sắc hoặc nhãn.'], ['status', 'TEXT', '', 'Trạng thái xử lý asset.'], ['created_at', 'TIMESTAMPTZ', '', 'Thời điểm asset được đăng lên.']
  ]),
  table('garments', 'Catalogue các món đồ số', [
    ['id', 'UUID', 'PK', 'Mã của một món đồ trong tủ số.'], ['user_id', 'UUID', 'FK → users', 'Người sở hữu món đồ.'], ['source_asset_id', 'UUID', 'FK → media_assets', 'Ảnh gốc dùng để tạo catalogue.'], ['category', 'TEXT', '', 'Nhóm đồ, ví dụ áo, quần, váy hoặc giày.'], ['color', 'TEXT', '', 'Màu chính của món đồ.'], ['material', 'TEXT', '', 'Chất liệu, nếu đã xác định.'], ['season', 'TEXT', '', 'Mùa hoặc điều kiện phù hợp.'], ['tags', 'JSONB', '', 'Nhãn bổ sung để tìm kiếm và phối đồ.'], ['wear_count', 'INTEGER', '', 'Số lần người dùng đã mặc.'], ['last_worn_at', 'TIMESTAMPTZ nullable', '', 'Lần mặc gần nhất, nếu có dữ liệu.'], ['catalog_status', 'TEXT', '', 'Trạng thái hiển thị trong catalogue.']
  ]),
  table('lookbooks', 'Bộ sưu tập theo chủ đề hoặc dịp', [
    ['id', 'UUID', 'PK', 'Mã của một bộ lookbook.'], ['user_id', 'UUID', 'FK → users', 'Người tạo lookbook.'], ['title', 'TEXT', '', 'Tên do người dùng đặt.'], ['theme', 'TEXT', '', 'Chủ đề phong cách, ví dụ tối giản hoặc vintage.'], ['occasion', 'TEXT', '', 'Dịp sử dụng, ví dụ đi làm hoặc đi chơi.'], ['status', 'TEXT', '', 'Trạng thái soạn, đã duyệt hoặc đã đăng.'], ['cover_asset_id', 'UUID', 'FK → media_assets', 'Ảnh bìa của lookbook.'], ['created_at', 'TIMESTAMPTZ', '', 'Thời điểm tạo lookbook.']
  ]),
  table('looks', 'Một biến thể outfit/ảnh AI đã tạo', [
    ['id', 'UUID', 'PK', 'Mã của một phương án outfit.'], ['lookbook_id', 'UUID', 'FK → lookbooks', 'Lookbook chứa phương án này.'], ['generated_asset_id', 'UUID', 'FK → media_assets', 'Ảnh AI tạo ra cho phương án.'], ['generation_prompt', 'TEXT', '', 'Yêu cầu đã dùng để tạo hoặc chỉnh ảnh.'], ['model_reference', 'TEXT', '', 'Tên hoặc phiên bản model đã xử lý.'], ['cmax_score', 'NUMERIC', '', 'Điểm chất lượng dùng để đánh giá kết quả AI.'], ['status', 'TEXT', '', 'Trạng thái tạo ảnh, chờ duyệt hoặc đã chấp nhận.'], ['created_at', 'TIMESTAMPTZ', '', 'Thời điểm tạo phương án.']
  ]),
  table('look_items', 'Món đồ cấu thành một look', [
    ['id', 'UUID', 'PK', 'Mã của một món đồ trong outfit.'], ['look_id', 'UUID', 'FK → looks', 'Outfit chứa món đồ này.'], ['garment_id', 'UUID', 'FK → garments', 'Món đồ được lấy từ catalogue.'], ['role', 'TEXT', '', 'Vai trò khi phối, ví dụ áo chính hoặc phụ kiện.'], ['layer_order', 'INTEGER', '', 'Thứ tự lớp khi hiển thị trên ảnh.']
  ]),
  table('ai_jobs', 'Theo dõi tác vụ AI và kết quả', [
    ['id', 'UUID', 'PK', 'Mã theo dõi một tác vụ AI.'], ['user_id', 'UUID', 'FK → users', 'Người yêu cầu tác vụ.'], ['job_type', 'TEXT', '', 'Loại việc, ví dụ trích xuất đồ hoặc tạo lookbook.'], ['input_ref', 'JSONB', '', 'Tham chiếu đến dữ liệu đầu vào của tác vụ.'], ['output_ref', 'JSONB', '', 'Tham chiếu đến kết quả hoặc asset đầu ra.'], ['model_provider', 'TEXT', '', 'Dịch vụ/model đã xử lý tác vụ.'], ['status', 'TEXT', '', 'Trạng thái hàng đợi, đang chạy, thành công hoặc lỗi.'], ['created_at', 'TIMESTAMPTZ', '', 'Thời điểm tạo tác vụ.'], ['completed_at', 'TIMESTAMPTZ nullable', '', 'Thời điểm tác vụ kết thúc, nếu có.']
  ]),
  table('approval_requests', 'Người dùng duyệt các thao tác nhạy cảm', [
    ['id', 'UUID', 'PK', 'Mã của một yêu cầu xác nhận.'], ['user_id', 'UUID', 'FK → users', 'Người cần đưa ra quyết định.'], ['subject_type', 'TEXT', '', 'Loại đối tượng cần duyệt, ví dụ look hoặc asset.'], ['subject_id', 'UUID', '', 'Mã đối tượng cần duyệt.'], ['action', 'TEXT', '', 'Hành động dự kiến, ví dụ lưu hoặc đăng.'], ['status', 'TEXT', '', 'Kết quả đang chờ, đã chấp nhận hoặc từ chối.'], ['requested_at', 'TIMESTAMPTZ', '', 'Thời điểm gửi yêu cầu.'], ['decided_at', 'TIMESTAMPTZ nullable', '', 'Thời điểm người dùng đưa ra quyết định.']
  ]),
  table('style_signals', 'Tín hiệu Swipe để học gu', [
    ['id', 'UUID', 'PK', 'Mã của một tín hiệu về gu.'], ['user_id', 'UUID', 'FK → users', 'Người đã swipe.'], ['look_id', 'UUID', 'FK → looks', 'Outfit được đánh giá.'], ['signal', 'TEXT', '', 'Loại phản hồi, ví dụ thích hoặc bỏ qua.'], ['weight', 'NUMERIC', '', 'Mức độ ảnh hưởng của phản hồi vào hồ sơ gu.'], ['created_at', 'TIMESTAMPTZ', '', 'Thời điểm ghi nhận swipe.']
  ]),
  table('taste_matches', 'Kết nối P2 “bạn cùng gu”', [
    ['id', 'UUID', 'PK', 'Mã của một gợi ý kết nối.'], ['user_id', 'UUID', 'FK → users', 'Người dùng hiện tại.'], ['matched_user_id', 'UUID', 'FK → users', 'Người có gu tương đồng được gợi ý.'], ['similarity_score', 'NUMERIC', '', 'Điểm cho biết hai gu mặc giống nhau đến mức nào.'], ['status', 'TEXT', '', 'Trạng thái gợi ý, ví dụ mới, đã xem hoặc đã kết nối.'], ['created_at', 'TIMESTAMPTZ', '', 'Thời điểm tạo gợi ý.']
  ])
].join('');

const relationMap = `
  <section class="relation-map" aria-labelledby="relation-map-title">
    <div class="relation-map__heading"><div><p class="eyebrow">Database Relation</p><h2 id="relation-map-title">Dữ liệu đi qua Garblo như thế nào?</h2><p>Đường nối thể hiện khoá ngoại giữa các bảng. RustFS nằm ngoài PostgreSQL và chỉ lưu tệp ảnh; database lưu khoá tham chiếu cùng thông tin mô tả.</p></div><div class="relation-map__legend"><span><i class="legend-dot entity-dot"></i>Bảng PostgreSQL</span><span><i class="legend-dot store-dot"></i>RustFS object store</span><span><i class="legend-line"></i>Khoá ngoại / tham chiếu</span></div></div>
    <div class="relation-map__canvas">
      <svg viewBox="0 0 1420 690" role="img" aria-label="Sơ đồ quan hệ bảng PostgreSQL và RustFS của Garblo">
        <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#7990aa"/></marker></defs>
        <rect class="db-boundary" x="24" y="34" width="1185" height="620" rx="22"/>
        <text class="boundary-label" x="54" y="73">PostgreSQL · quan hệ dữ liệu</text>
        <path class="edge" d="M280 326 L330 160"/><text class="edge-label" x="288" y="228">user_id</text>
        <path class="edge" d="M280 326 L330 286"/><text class="edge-label" x="286" y="300">user_id</text>
        <path class="edge" d="M280 342 L330 412"/><text class="edge-label" x="284" y="384">user_id</text>
        <path class="edge" d="M280 358 L330 537"/><text class="edge-label" x="286" y="458">user_id</text>
        <path class="edge" d="M280 374 L560 537"/><text class="edge-label" x="408" y="474">user_id</text>
        <path class="edge" d="M280 310 L560 160"/><text class="edge-label" x="412" y="210">user_id</text>
        <path class="edge" d="M280 336 L560 326"/><text class="edge-label" x="414" y="319">user_id</text>
        <path class="edge" d="M280 352 L800 326"/><text class="edge-label" x="518" y="368">user_id</text>
        <path class="edge accent" d="M790 160 L1240 160"/><text class="edge-label accent-label" x="958" y="145">object_key</text>
        <path class="edge" d="M665 205 L665 280"/><text class="edge-label" x="678" y="247">source_asset_id</text>
        <path class="edge" d="M770 175 L905 280"/><text class="edge-label" x="806" y="224">cover_asset_id</text>
        <path class="edge" d="M770 190 L1145 280"/><text class="edge-label" x="943" y="247">generated_asset_id</text>
        <path class="edge" d="M1010 326 L1040 326"/><text class="edge-label" x="1008" y="306">lookbook_id</text>
        <path class="edge" d="M1150 376 L1150 465"/><text class="edge-label" x="1162" y="424">look_id</text>
        <path class="edge" d="M770 342 L1040 512"/><text class="edge-label" x="870" y="456">garment_id</text>
        <path class="edge" d="M1040 350 L440 286"/><text class="edge-label" x="744" y="332">look_id</text>
        <g class="entity"><rect x="80" y="280" width="200" height="112" rx="14"/><text x="104" y="320">users</text><text class="node-sub" x="104" y="346">Tài khoản & gu mặc</text></g>
        <g class="entity"><rect x="330" y="100" width="170" height="92" rx="14"/><text x="352" y="136">consent_records</text><text class="node-sub" x="352" y="161">Lịch sử đồng ý</text></g>
        <g class="entity"><rect x="330" y="225" width="170" height="92" rx="14"/><text x="352" y="261">style_signals</text><text class="node-sub" x="352" y="286">Swipe học gu</text></g>
        <g class="entity"><rect x="330" y="350" width="170" height="92" rx="14"/><text x="352" y="386">taste_matches</text><text class="node-sub" x="352" y="411">Bạn cùng gu</text></g>
        <g class="entity"><rect x="330" y="475" width="170" height="92" rx="14"/><text x="352" y="511">ai_jobs</text><text class="node-sub" x="352" y="536">Tác vụ AI</text></g>
        <g class="entity"><rect x="560" y="475" width="205" height="92" rx="14"/><text x="584" y="511">approval_requests</text><text class="node-sub" x="584" y="536">Yêu cầu xác nhận</text></g>
        <g class="entity primary"><rect x="560" y="100" width="230" height="105" rx="14"/><text x="586" y="140">media_assets</text><text class="node-sub" x="586" y="166">Thông tin mô tả ảnh</text><text class="node-sub" x="586" y="188">+ object_key RustFS</text></g>
        <g class="entity"><rect x="560" y="280" width="210" height="92" rx="14"/><text x="584" y="316">garments</text><text class="node-sub" x="584" y="341">Catalogue tủ đồ</text></g>
        <g class="entity"><rect x="800" y="280" width="210" height="92" rx="14"/><text x="824" y="316">lookbooks</text><text class="node-sub" x="824" y="341">Bộ sưu tập</text></g>
        <g class="entity"><rect x="1040" y="280" width="200" height="92" rx="14"/><text x="1064" y="316">looks</text><text class="node-sub" x="1064" y="341">Một phương án outfit</text></g>
        <g class="entity"><rect x="1040" y="465" width="200" height="92" rx="14"/><text x="1064" y="501">look_items</text><text class="node-sub" x="1064" y="526">Món đồ trong outfit</text></g>
        <g class="store"><rect x="1240" y="110" width="150" height="100" rx="14"/><text x="1264" y="150">RustFS</text><text class="node-sub" x="1264" y="176">Tệp ảnh</text></g>
      </svg>
    </div>
  </section>`;

const html = `<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Garblo · Kiến trúc giải pháp</title>
  <style>
    :root { --ink:#172033; --muted:#697386; --line:#dce3ee; --page:#f4f7fb; --panel:#fff; --navy:#12233f; --cyan:#0b9bb4; --violet:#7257d8; --soft:#e8f6f8; }
    * { box-sizing:border-box; } body { margin:0; color:var(--ink); background:var(--page); font-family:Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; }
    .shell { width:min(1480px, calc(100% - 40px)); margin:24px auto 42px; }
    .hero { color:#fff; background:linear-gradient(120deg,#11233d,#19556b 55%,#287a86); border-radius:20px 20px 0 0; padding:28px 34px 23px; box-shadow:0 18px 40px rgba(16,34,60,.14); }
    .eyebrow { margin:0 0 7px; font-size:12px; font-weight:800; letter-spacing:.13em; text-transform:uppercase; color:#9ee6e5; }
    h1 { margin:0; font-size:clamp(26px,4vw,42px); letter-spacing:-.04em; } .hero p { margin:9px 0 0; max-width:820px; color:#d5eaf1; line-height:1.55; }
    .tabs { display:flex; gap:6px; padding:12px 14px; background:#fff; border:1px solid var(--line); border-top:0; }
    [role="tab"] { appearance:none; border:0; background:transparent; color:var(--muted); padding:11px 16px; border-radius:9px; font:700 14px inherit; cursor:pointer; }
    [role="tab"][aria-selected="true"] { color:#fff; background:var(--navy); box-shadow:0 3px 8px rgba(18,35,63,.18); }
    [role="tabpanel"] { border:1px solid var(--line); border-top:0; background:var(--panel); min-height:720px; } [hidden] { display:none !important; }
    .architecture-frame,.database-frame { display:block; width:100%; height:calc(100vh - 150px); min-height:760px; border:0; background:#fff; }
    .schema { padding:30px; } .schema-intro { display:grid; grid-template-columns:1fr auto; gap:20px; align-items:start; padding:23px; border:1px solid #cfe9ec; border-radius:15px; background:linear-gradient(110deg,#f0fbfc,#f8f7ff); }
    .schema h2 { margin:0 0 7px; font-size:24px; letter-spacing:-.025em; } .schema p { margin:0; color:var(--muted); line-height:1.55; }
    .note { margin:20px 0; padding:14px 16px; border-left:4px solid var(--cyan); background:#f1f9fb; color:#315060; line-height:1.5; }
    .relation-map { padding:30px; border-bottom:1px solid var(--line); background:linear-gradient(135deg,#f8fbff,#f1f8fb); } .relation-map__heading { display:flex; justify-content:space-between; gap:24px; align-items:start; margin-bottom:20px; } .relation-map .eyebrow { color:#15869b; } .relation-map h2 { margin:0 0 7px; font-size:24px; letter-spacing:-.025em; } .relation-map p { max-width:760px; margin:0; color:var(--muted); line-height:1.55; } .relation-map__legend { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:10px 14px; padding-top:8px; color:#59667a; font-size:11px; font-weight:700; } .relation-map__legend span { display:flex; gap:6px; align-items:center; white-space:nowrap; } .legend-dot { width:10px; height:10px; border-radius:50%; } .entity-dot { background:#1e98b5; } .store-dot { background:#ec8c38; } .legend-line { width:18px; height:2px; background:#7990aa; position:relative; } .legend-line::after { content:''; position:absolute; right:-1px; top:-3px; border-left:5px solid #7990aa; border-top:4px solid transparent; border-bottom:4px solid transparent; } .relation-map__canvas { overflow:auto; padding:8px; border:1px solid #d8e4ef; border-radius:16px; background:#fff; } .relation-map svg { display:block; width:100%; min-width:1000px; height:auto; font-family:Inter,ui-sans-serif,system-ui,sans-serif; } .db-boundary { fill:#f9fcff; stroke:#9db9ce; stroke-width:2; stroke-dasharray:8 6; } .boundary-label { fill:#54738d; font-size:15px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; } .edge { fill:none; stroke:#7990aa; stroke-width:2.5; marker-end:url(#arrow); } .edge.accent { stroke:#e28b38; } .edge-label { fill:#60778f; font:600 12px ui-monospace,SFMono-Regular,Consolas,monospace; paint-order:stroke; stroke:#fff; stroke-width:5px; stroke-linejoin:round; } .accent-label { fill:#b66821; } .entity rect { fill:#f6fcfe; stroke:#2696b2; stroke-width:2; } .entity.primary rect { fill:#eaf8fb; stroke:#15869b; stroke-width:2.5; } .store rect { fill:#fff7ee; stroke:#ec8c38; stroke-width:2.5; } .entity text,.store text { fill:#17405b; font-size:17px; font-weight:800; } .entity text.node-sub,.store text.node-sub { fill:#5d6f81; font-size:13px; font-weight:500; }
    .table-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(310px,1fr)); gap:16px; } .table-card { overflow:hidden; border:1px solid var(--line); border-radius:13px; background:#fff; box-shadow:0 5px 14px rgba(20,36,60,.045); }
    .table-card header { padding:16px 17px 13px; border-bottom:1px solid var(--line); background:linear-gradient(115deg,#fff,#f5f9fc); } .table-card h3 { margin:0; color:#183959; font:800 18px ui-monospace,SFMono-Regular,Consolas,monospace; } .table-card p { margin:6px 0 0; min-height:38px; color:var(--muted); font-size:12px; line-height:1.45; }
    .table-card ul { margin:0; padding:8px 0; list-style:none; } .table-card li { display:grid; grid-template-columns:minmax(125px,1fr) auto auto; gap:4px 8px; align-items:center; padding:8px 17px; border-bottom:1px solid #f0f3f7; font-size:12px; } .table-card li:last-child { border-bottom:0; } code { color:#1b5471; font:700 12px ui-monospace,SFMono-Regular,Consolas,monospace; } .table-card span { color:#687386; font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:11px; } em { padding:2px 5px; border-radius:4px; color:#694d9d; background:#f0ebff; font-style:normal; font-size:10px; font-weight:800; } .table-card small { grid-column:1 / -1; color:#526176; font-size:11px; line-height:1.35; }
    @media (max-width:700px) { .shell { width:100%; margin:0; } .hero { border-radius:0; padding:24px 20px; } .tabs { overflow:auto; padding:10px; } [role="tab"] { white-space:nowrap; } .relation-map,.schema { padding:18px; } .relation-map__heading { display:block; } .relation-map__legend { justify-content:flex-start; margin-top:13px; } .schema-intro { grid-template-columns:1fr; } .architecture-frame,.database-frame { height:800px; min-height:0; } .table-card li { grid-template-columns:minmax(112px,1fr) auto; } .table-card li em { grid-column:1 / -1; justify-self:start; } }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero"><p class="eyebrow">VJAI Hackathon 2026 · Sơ đồ giải pháp</p><h1>Garblo · Từ ảnh rời rạc đến tủ đồ được mặc lại</h1><p>Bản thiết kế cho ứng dụng dùng AI để sắp xếp tủ đồ số, tạo lookbook và giúp bạn phối lại đồ đang có.</p></section>
    <nav class="tabs" role="tablist" aria-label="Các phần của giải pháp Garblo">
      <button id="architecture-tab" role="tab" aria-selected="true" aria-controls="architecture-panel" tabindex="0">Kiến trúc giải pháp</button>
      <button id="database-tab" role="tab" aria-selected="false" aria-controls="database-panel" tabindex="-1">Database Relation</button>
    </nav>
    <section id="architecture-panel" role="tabpanel" aria-labelledby="architecture-tab"><iframe class="architecture-frame" title="Garblo architecture diagram" sandbox="allow-scripts allow-downloads"></iframe></section>
    <section id="database-panel" role="tabpanel" aria-labelledby="database-tab" hidden>
      <iframe class="database-frame" title="Garblo database relation diagram" sandbox="allow-scripts allow-downloads"></iframe>
      <div class="schema"><div class="schema-intro"><div><h2>Chi tiết các bảng</h2><p>Mô tả field chính cho catalogue, tạo ảnh bằng AI, phê duyệt, swipe học gu và Social P2.</p></div></div>
      <p class="note"><strong>Quy tắc lưu trữ:</strong> Tệp ảnh không nằm trong PostgreSQL. <code>media_assets.object_key</code> và <code>looks.generated_asset_id</code> liên kết thông tin mô tả tới ảnh tương ứng ở RustFS.</p>
      <div class="table-grid">${tables}</div></div>
    </section>
  </main>
  <script id="architecture-source" type="application/octet-stream">${architectureBase64}</script>
  <script id="database-source" type="application/octet-stream">${databaseRelationBase64}</script>
  <script>
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
    const activate = (tab) => { tabs.forEach((item, index) => { const active = item === tab; item.setAttribute('aria-selected', String(active)); item.tabIndex = active ? 0 : -1; panels[index].hidden = !active; }); };
    tabs.forEach((tab, index) => { tab.addEventListener('click', () => activate(tab)); tab.addEventListener('keydown', event => { if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; tabs[next].focus(); activate(tabs[next]); }); });
    const embed = (selector, sourceId) => {
      const source = document.getElementById(sourceId).textContent.trim();
      const bytes = Uint8Array.from(atob(source), char => char.charCodeAt(0));
      document.querySelector(selector).srcdoc = new TextDecoder().decode(bytes);
    };
    embed('.architecture-frame', 'architecture-source');
    embed('.database-frame', 'database-source');
  </script>
</body>
</html>`;

await writeFile('output/garblo-solution.html', html, 'utf8');
