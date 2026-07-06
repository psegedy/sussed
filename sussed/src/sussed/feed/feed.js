(() => {
  "use strict";

  const payload = JSON.parse(document.getElementById("feed-data").textContent);
  const postsById = payload.posts || {};
  const context = payload.context || {};
  const state = {
    tab: "ai_picks",
    sort: "score",
    listingType: "all",
    propertyType: "all",
    freshWindow: "all",
  };

  const feed = document.getElementById("feed");
  const sortSelect = document.getElementById("sort-select");
  const listingFilter = document.getElementById("listing-filter");
  const propertyFilter = document.getElementById("property-filter");
  const freshWindow = document.getElementById("fresh-window");

  const money = new Intl.NumberFormat("cs-CZ", { maximumFractionDigits: 0 });
  const pct = new Intl.NumberFormat("cs-CZ", { maximumFractionDigits: 1 });

  function text(value, fallback = "") {
    return value === null || value === undefined || value === "" ? fallback : String(value);
  }

  function safeHttpUrl(value) {
    try {
      const url = new URL(String(value));
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : "#";
    } catch {
      return "#";
    }
  }

  function create(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function append(parent, ...children) {
    children.filter(Boolean).forEach((child) => parent.appendChild(child));
    return parent;
  }

  function fmtMoney(value) {
    return `${money.format(Number(value || 0))}`;
  }

  function fmtDate(iso) {
    return iso ? String(iso).slice(0, 10) : null;
  }

  function normalize(value) {
    return text(value).trim().toLowerCase();
  }

  function scoreBadge(post) {
    const score = post.score;
    let icon = "❓";
    let className = "badge";
    if (score === 9999) {
      icon = "🦄";
      className += " good";
    } else if (score >= 800) {
      icon = "🔥";
      className += " good";
    } else if (score >= 600) {
      icon = "✅";
      className += " good";
    } else if (score >= 400) {
      icon = "😐";
    } else if (score >= 0) {
      icon = "👎";
    } else if (score === -1) {
      icon = "🚨";
      className += " danger";
    }
    return create("span", className, `${icon} ${score === null || score === undefined ? "?" : score}`);
  }

  function makeChip(className, label) {
    return create("span", `chip ${className}`.trim(), label);
  }

  function makePlaceholder() {
    return create("div", "placeholder", "🏠");
  }

  function currentIds() {
    const ids = Array.isArray(payload[state.tab]) ? payload[state.tab] : [];
    return ids.map((id) => postsById[id]).filter(Boolean);
  }

  function propertyMatches(post) {
    if (state.propertyType === "all") return true;
    const category = normalize(post.property_category);
    return category === state.propertyType || category.includes(state.propertyType);
  }

  function listingMatches(post) {
    return state.listingType === "all" || normalize(post.listing_type) === state.listingType;
  }

  function freshDate(post) {
    // Mirror the backend Fresh window: portal date, falling back to first-seen.
    return post.source_updated_at || post.first_seen_at || null;
  }

  function freshMatches(post) {
    if (state.tab !== "fresh" || state.freshWindow === "all") return true;
    const rawDate = freshDate(post);
    if (!rawDate) return false;
    const generated = context.generated_at ? new Date(context.generated_at) : new Date();
    const requestedDays = state.freshWindow === "day" ? 2 : 7;
    const maxDays = Number(context.fresh_days);
    const days =
      Number.isFinite(maxDays) && maxDays > 0 ? Math.min(requestedDays, maxDays) : requestedDays;
    return generated.getTime() - new Date(rawDate).getTime() <= days * 24 * 60 * 60 * 1000;
  }

  function sortPosts(posts) {
    const list = [...posts];
    const poaBottom = (a, b) => Number(Boolean(a.is_poa)) - Number(Boolean(b.is_poa));
    list.sort((a, b) => {
      if (state.sort === "price_asc" || state.sort === "price_desc") {
        const poa = poaBottom(a, b);
        if (poa !== 0) return poa;
        const av = Number(a.price_czk || 0);
        const bv = Number(b.price_czk || 0);
        return state.sort === "price_asc" ? av - bv : bv - av;
      }
      if (state.sort === "newest") {
        return new Date(b.first_seen_at || 0).getTime() - new Date(a.first_seen_at || 0).getTime();
      }
      if (state.sort === "biggest_drop") {
        const av = typeof a.last_change_percent === "number" ? a.last_change_percent : Number.POSITIVE_INFINITY;
        const bv = typeof b.last_change_percent === "number" ? b.last_change_percent : Number.POSITIVE_INFINITY;
        return av - bv;
      }
      return Number(b.score ?? -9999) - Number(a.score ?? -9999);
    });
    return list;
  }

  function filteredPosts() {
    return sortPosts(currentIds().filter((post) => listingMatches(post) && propertyMatches(post) && freshMatches(post)));
  }

  function renderMedia(post) {
    const media = create("section", "media");
    media.tabIndex = 0;
    media.setAttribute("aria-label", "Listing image carousel");

    const track = create("div", "track");
    const urls = Array.isArray(post.image_urls) && post.image_urls.length ? post.image_urls : [null];
    urls.forEach((url, index) => {
      const slide = create("div", "slide");
      if (url) {
        const img = document.createElement("img");
        img.loading = "lazy";
        img.decoding = "async";
        img.alt = `${text(post.title, "Listing image")} · ${index + 1}`;
        img.src = safeHttpUrl(url);
        img.onerror = () => slide.replaceChildren(makePlaceholder());
        slide.appendChild(img);
      } else {
        slide.appendChild(makePlaceholder());
      }
      track.appendChild(slide);
    });

    const chips = create("div", "media-chips");
    if (post.has_video) chips.appendChild(makeChip("", "🎥 video"));
    if (post.has_3d_tour) chips.appendChild(makeChip("", "🧊 3D tour"));
    if (post.has_floor_plan) chips.appendChild(makeChip("", "📐 půdorys"));

    const prev = create("button", "media-btn prev", "‹");
    const next = create("button", "media-btn next", "›");
    prev.type = "button";
    next.type = "button";
    prev.setAttribute("aria-label", "Previous image");
    next.setAttribute("aria-label", "Next image");

    const dots = create("div", "dots");
    const dotButtons = urls.map((_, index) => {
      const dot = create("button", `dot${index === 0 ? " is-active" : ""}`);
      dot.type = "button";
      dot.setAttribute("aria-label", `Go to image ${index + 1}`);
      dot.addEventListener("click", () => track.scrollTo({ left: index * track.clientWidth, behavior: "smooth" }));
      dots.appendChild(dot);
      return dot;
    });

    function move(direction) {
      track.scrollBy({ left: direction * track.clientWidth, behavior: "smooth" });
    }
    prev.addEventListener("click", () => move(-1));
    next.addEventListener("click", () => move(1));
    media.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") move(-1);
      if (event.key === "ArrowRight") move(1);
    });
    track.addEventListener("scroll", () => {
      const active = Math.round(track.scrollLeft / Math.max(track.clientWidth, 1));
      dotButtons.forEach((dot, index) => dot.classList.toggle("is-active", index === active));
    }, { passive: true });

    append(media, track, chips);
    if (urls.length > 1) append(media, prev, next, dots);
    return media;
  }

  function renderPrice(post) {
    const row = create("div", "price-row");
    const price = create("span", "price", post.is_poa ? "Cena na dotaz" : fmtMoney(post.price_czk));
    const unit = create("span", "unit", post.is_poa ? "" : post.listing_type === "rent" ? "Kč/měsíc" : "Kč");
    append(row, price, unit);

    if (post.dropped_to_poa && post.original_price) {
      row.appendChild(create("span", "chip delta warn", `↓ z ${fmtMoney(post.original_price)}`));
    } else if (post.change_direction) {
      const direction = post.change_direction === "decrease" ? "down" : "up";
      const arrow = direction === "down" ? "↓" : "↑";
      const change = typeof post.last_change_percent === "number" ? `${pct.format(Math.abs(post.last_change_percent))}%` : fmtMoney(Math.abs(post.last_change_amount || 0));
      row.appendChild(create("span", `chip delta ${direction}`, `${arrow} ${change}`));
      if (post.initial_price) row.appendChild(create("span", "chip", `z ${fmtMoney(post.initial_price)}`));
    }
    return row;
  }

  function renderStats(post) {
    const stats = create("div", "stat-strip");
    const firstSeen = fmtDate(post.first_seen_at);
    if (firstSeen) stats.appendChild(create("span", null, `📅 přidáno ${firstSeen}`));
    const updated = fmtDate(post.source_updated_at);
    if (updated) stats.appendChild(create("span", null, `· aktualizováno ${updated}`));
    stats.appendChild(create("span", null, `🖼 ${post.image_count || (post.image_urls || []).length || 0}`));
    if (post.area_m2) stats.appendChild(create("span", null, `${post.area_m2} m²`));
    if (post.price_per_m2) stats.appendChild(create("span", null, `${fmtMoney(post.price_per_m2)} Kč/m²`));
    return stats;
  }

  function renderChipRows(post) {
    const frag = document.createDocumentFragment();
    const groups = [
      [post.pros, "pro", "✅"],
      [post.cons_red, "red", "🚩"],
      [post.cons_yellow, "yellow", "⚠️"],
    ];
    groups.forEach(([items, className, icon]) => {
      if (!Array.isArray(items) || items.length === 0) return;
      const row = create("div", "chip-row");
      items.forEach((item) => row.appendChild(makeChip(className, `${icon} ${item}`)));
      frag.appendChild(row);
    });
    return frag;
  }

  function renderCosts(post) {
    const entries = [];
    if (post.parking_price) entries.push(["Parkování", `${fmtMoney(post.parking_price)} Kč${post.parking_included ? " · v ceně" : " · extra"}`]);
    else if (post.parking_included === true) entries.push(["Parkování", "v ceně"]);
    else if (post.parking_included === false) entries.push(["Parkování", "není v ceně / nejasné"]);

    Object.entries(post.hidden_costs || {}).forEach(([key, value]) => {
      if (value === null || value === undefined || value === "") return;
      entries.push([key.replaceAll("_", " "), typeof value === "object" ? JSON.stringify(value) : String(value)]);
    });

    if (entries.length === 0) return null;
    const box = create("aside", "costs");
    box.appendChild(create("h3", null, "Skryté náklady"));
    const list = create("ul");
    entries.forEach(([key, value]) => {
      const item = create("li");
      item.appendChild(create("strong", null, `${key}: `));
      item.appendChild(document.createTextNode(value));
      list.appendChild(item);
    });
    box.appendChild(list);
    return box;
  }

  function renderDetails(post) {
    const details = create("section", "details");
    details.appendChild(renderPrice(post));
    details.appendChild(renderStats(post));

    const title = create("a", "title-link", text(post.title, "Bez názvu"));
    title.href = safeHttpUrl(post.url);
    title.target = "_blank";
    title.rel = "noopener noreferrer";
    details.appendChild(title);

    if (post.summary) details.appendChild(create("p", "summary", post.summary));
    details.appendChild(renderChipRows(post));

    const costs = renderCosts(post);
    if (costs) details.appendChild(costs);

    const cta = create("a", "cta", "Zobrazit na sreality →");
    cta.href = safeHttpUrl(post.url);
    cta.target = "_blank";
    cta.rel = "noopener noreferrer";
    details.appendChild(cta);
    return details;
  }

  function renderPost(post, index) {
    const card = create("article", "post");
    card.style.animationDelay = `${Math.min(index * 70, 560)}ms`;

    const top = create("header", "post__top");
    const agency = text(post.agency_name, post.source || "?");
    const avatar = create("div", "avatar", text(agency).slice(0, 1) || "?");
    const meta = create("div", "meta");
    meta.appendChild(create("strong", null, [post.district, post.city].filter(Boolean).join(" · ") || "Neznámá lokace"));
    meta.appendChild(create("span", null, agency));
    const badges = create("div", "badges");
    badges.appendChild(scoreBadge(post));
    badges.appendChild(create("span", post.is_reviewed ? "pill reviewed" : "pill", post.is_reviewed ? "AI reviewed" : "quick score"));
    append(top, avatar, meta, badges);

    const body = create("div", "post__body");
    append(body, renderMedia(post), renderDetails(post));
    append(card, top, body);
    return card;
  }

  function renderEmpty() {
    const empty = create("section", "empty");
    empty.appendChild(create("h2", null, "Zatím tu nic není"));
    empty.appendChild(create("p", null, "Spusť `sussed hunt` / `sussed review` a tahle galerie se začne plnit."));
    return empty;
  }

  function render() {
    document.querySelectorAll(".tab").forEach((tab) => {
      const active = tab.dataset.tab === state.tab;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-pressed", String(active));
    });
    freshWindow.hidden = state.tab !== "fresh";

    const posts = filteredPosts();
    feed.replaceChildren();
    if (posts.length === 0) {
      feed.appendChild(renderEmpty());
      return;
    }
    posts.forEach((post, index) => feed.appendChild(renderPost(post, index)));
  }

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.tab = tab.dataset.tab;
      render();
    });
  });

  sortSelect.addEventListener("change", () => { state.sort = sortSelect.value; render(); });
  listingFilter.addEventListener("change", () => { state.listingType = listingFilter.value; render(); });
  propertyFilter.addEventListener("change", () => { state.propertyType = propertyFilter.value; render(); });
  document.querySelectorAll(".seg").forEach((button) => {
    button.addEventListener("click", () => {
      state.freshWindow = button.dataset.window;
      document.querySelectorAll(".seg").forEach((seg) => seg.classList.toggle("is-active", seg === button));
      render();
    });
  });

  document.querySelector('[data-count="ai_picks"]').textContent = String((payload.ai_picks || []).length);
  document.querySelector('[data-count="fresh"]').textContent = String((payload.fresh || []).length);
  render();
})();
