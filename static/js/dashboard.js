/* Hardware Comps Dashboard — client-side UX.
   Table switching, search/filter, pagination, add/edit modal,
   comp lookup modal, batch comps, sparklines, toasts. */

(function () {
  "use strict";

  var API_BASE = "";
  var TOKEN = null;          // set from env / injected by server if needed
  var currentTable = "personal_assets";
  var currentPage = 0;
  var pageSize = 25;
  var totalItems = 0;
  var allItems = [];        // full filtered list for pagination

  /* ── Init ── */
  document.addEventListener("DOMContentLoaded", function () {
    wireTableSwitcher();
    wireAddItemModal();
    wireRefreshComps();
    wireBatchComps();
    wireSearchFilter();
    wirePager();
    wireTableRowClicks();
    // Initial load of the active table
    loadTable(currentTable, 0);
    // If RAM/SSD tab, load memory market sidebar
    if (currentTable === "ram_ssd_stockpile") {
      loadMemoryMarket();
    }
  });

  /* ── Auth: read token from a global injected by server, or prompt once ── */
  function getToken() {
    if (TOKEN) return TOKEN;
    // Try server-injected window.HCD_TOKEN (Jinja can write it into a script tag
    // on the dashboard page if the user is already authenticated via session).
    if (window.HCD_TOKEN) {
      TOKEN = window.HCD_TOKEN;
      return TOKEN;
    }
    // Fallback: prompt once and remember for the session.
    var t = prompt("Enter HCD API token:");
    if (t) {
      TOKEN = t;
      return t;
    }
    return null;
  }

  function authHeaders() {
    var t = getToken();
    return t ? { "Authorization": "Bearer " + t } : {};
  }

  function apiPath(path) {
    return API_BASE + path;
  }

  function fetchJSON(path, options) {
    var opts = Object.assign({
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
    }, options);
    return fetch(apiPath(path), opts).then(function (r) {
      if (r.status === 401) {
        toast("Auth failed — check your API token", "error");
        TOKEN = null;
        throw new Error("unauthorized");
      }
      if (r.status === 403 || r.status === 404) {
        throw new Error("not found");
      }
      if (!r.ok) {
        return r.json().then(function (body) {
          throw new Error(body.detail || body.error || "API error " + r.status);
        }).catch(function () {
          throw new Error("API error " + r.status);
        });
      }
      return r.json();
    });
  }

  function fetchText(path, options) {
    var opts = Object.assign({
      headers: authHeaders(),
    }, options || {});
    return fetch(apiPath(path), opts).then(function (r) {
      if (!r.ok) throw new Error("fetch failed " + r.status);
      return r.text();
    });
  }

  /* ── Toast ── */
  function toast(msg, kind) {
    var existing = document.querySelector(".toast");
    if (existing) existing.remove();
    var el = document.createElement("div");
    el.className = "toast" + (kind === "error" ? " error" : kind === "success" ? " success" : "");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 4000);
  }

  /* ── Table switcher ── */
  function wireTableSwitcher() {
    var btns = document.querySelectorAll("#tableSwitcher .switcher-btn");
    btns.forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var table = btn.getAttribute("data-table");
        if (table === currentTable) return;
        // Update active state
        btns.forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        currentTable = table;
        currentPage = 0;
        allItems = [];
        // Toggle memory sidebar
        var sidebar = document.getElementById("memorySidebar");
        if (table === "ram_ssd_stockpile") {
          sidebar.hidden = false;
          loadMemoryMarket();
        } else {
          sidebar.hidden = true;
        }
        loadTable(table, 0);
      });
    });
  }

  /* ── Table headline stats ── */
  function renderStats(items) {
    var total = document.getElementById("statTotal");
    var soldAvg = document.getElementById("statSoldAvg");
    var activeAvg = document.getElementById("statActiveAvg");
    var portfolio = document.getElementById("statPortfolio");

    if (!items.length) {
      total.textContent = "0";
      soldAvg.textContent = "—";
      activeAvg.textContent = "—";
      portfolio.textContent = "—";
      return;
    }
    total.textContent = String(items.length);

    var soldVals = items.map(function (i) {
      if (i.ebay_sold_avg_cents) return i.ebay_sold_avg_cents;
      return null;
    }).filter(Boolean);
    var activeVals = items.map(function (i) {
      if (i.ebay_active_avg_cents) return i.ebay_active_avg_cents;
      return null;
    }).filter(Boolean);

    soldAvg.textContent = soldVals.length
      ? "$" + (soldVals.reduce(function (a, b) { return a + b; }, 0) / soldVals.length / 100).toFixed(2)
      : "—";
    activeAvg.textContent = activeVals.length
      ? "$" + (activeVals.reduce(function (a, b) { return a + b; }, 0) / activeVals.length / 100).toFixed(2)
      : "—";

    var portfolioVals = items.map(function (i) {
      var v = i.ebay_sold_avg_cents || i.ebay_active_avg_cents;
      return v || 0;
    });
    var pTotal = portfolioVals.reduce(function (a, b) { return a + b; }, 0);
    portfolio.textContent = pTotal > 0 ? "$" + (pTotal / 100).toFixed(2) : "—";
  }

  /* ── Load table (paginated) ── */
  function loadTable(table, page) {
    var body = document.getElementById("inventoryBody");
    body.innerHTML = '<tr><td colspan="10" class="empty-row shimmer">Loading…</td></tr>';

    var offset = page * pageSize;
    fetchJSON("/api/inventory?table_type=" + encodeURIComponent(table) + "&limit=" + pageSize + "&offset=" + offset)
      .then(function (data) {
        allItems = data.items || [];
        totalItems = data.total || 0;
        renderTableRows(allItems);
        renderStats(allItems);
        currentPage = page;
        updatePager();
      })
      .catch(function (err) {
        body.innerHTML = '<tr><td colspan="10" class="empty-row">Error loading inventory. ' + escapeHtml(err.message) + '</td></tr>';
      });
  }

  /* ── Render rows ── */
  function renderTableRows(items) {
    var body = document.getElementById("inventoryBody");
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="10" class="empty-row">No items yet. Click "+ Add Item" to get started.</td></tr>';
      return;
    }

    var html = "";
    items.forEach(function (item) {
      html += renderRow(item);
    });
    body.innerHTML = html;
  }

  function renderRow(item) {
    var sold = item.ebay_sold_avg_cents ? ("$" + (item.ebay_sold_avg_cents / 100).toFixed(2)) : "—";
    var active = item.ebay_active_avg_cents ? ("$" + (item.ebay_active_avg_cents / 100).toFixed(2)) : "—";
    var buy = item.acquisition_cost_cents ? ("$" + (item.acquisition_cost_cents / 100).toFixed(2)) : "—";
    var lastLooked = item.ebay_last_looked_at ? formatDate(item.ebay_last_looked_at) : "—";

    var trendHtml = sparklineHtml(item.id);

    var checked = "";
    if (item.id) {
      checked = '<td class="checkbox-cell"><input type="checkbox" class="row-checkbox" data-id="' + item.id + '" /></td>';
    }

    var actions = "";
    if (item.id) {
      actions = '<div class="table-actions">' +
        '<button class="btn lookup-comp-btn" data-id="' + item.id + '" title="Run eBay comps">Comps</button>' +
        '<button class="btn edit-item-btn" data-id="' + item.id + '" title="Edit">Edit</button>' +
        (item.table_type === "ram_ssd_stockpile" ? '<button class="btn log-event-btn" data-id="' + item.id + '" title="Log price event">Log event</button>' : "") +
        "</div>";
    }

    return '<tr>' +
      '<td><strong>' + escapeHtml(item.name) + '</strong></td>' +
      '<td><span class="table-type-badge ' + cssClass(item.table_type) + '">' + humanLabel(item.table_type) + '</span></td>' +
      '<td>' + escapeHtml(item.make || "") + (item.make && item.model ? " " : "") + escapeHtml(item.model || "") + '</td>' +
      '<td>' + (item.condition ? escapeHtml(item.condition) : "—") + '</td>' +
      '<td class="cents">' + buy + '</td>' +
      '<td class="cents">' + sold + '</td>' +
      '<td class="cents">' + active + '</td>' +
      trendHtml +
      '<td class="last-looked">' + lastLooked + '</td>' +
      (checked + actions) +
      '</tr>';
  }

  function cssClass(type) {
    return (type || "").replace(/_/g, "-");
  }

  function humanLabel(type) {
    var map = {
      "personal_assets": "Personal",
      "goodwill_flips": "Goodwill",
      "liquidation_pallets": "Pallets",
      "ram_ssd_stockpile": "RAM/SSD",
      "device_farm": "Devices",
    };
    return map[type] || type;
  }

  /* ── Sparklines (inline SVG) ── */
  function sparklineHtml(itemId) {
    // We render sparklines client-side from fetched price history (loaded on row hover or via a small data attr).
    // For initial render, show a placeholder that gets filled lazily.
    var dataHint = "";
    if (itemId) {
      dataHint = ' data-sparkline-id="' + itemId + '"';
    }
    return '<td class="sparkline-cell"><svg class="sparkline flat" viewBox="0 0 80 28" preserveAspectRatio="none"' + dataHint + '><path d=""/></svg></td>';
  }

  function renderSparkline(svgEl, snapshots) {
    if (!snapshots || snapshots.length < 2) {
      svgEl.classList.remove("up", "down");
      svgEl.classList.add("flat");
      svgEl.querySelector("path").setAttribute("d", "");
      svgEl.title = "No trend data";
      return;
    }
    // snapshots come in DESC order from API; reverse to time-ascending for the sparkline
    var sorted = snapshots.slice().sort(function (a, b) {
      return new Date(a.snapshot_at || 0) - new Date(b.snapshot_at || 0);
    });
    var values = sorted.map(function (s) {
      var v = s.sold_avg_cents || s.active_avg_cents;
      return v ? v / 100 : null;
    }).filter(function (v) { return v !== null && isFinite(v); });
    if (values.length < 2) {
      svgEl.classList.remove("up", "down");
      svgEl.classList.add("flat");
      svgEl.querySelector("path").setAttribute("d", "");
      svgEl.title = "Insufficient trend data";
      return;
    }
    var w = 80, h = 28;
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var range = Math.max(max - min, 0.01);
    var points = values.map(function (v, i) {
      var x = (i / (values.length - 1)) * w;
      var y = h - ((v - min) / range) * (h - 4) - 2;
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    var d = "M" + points.join(" L");
    svgEl.querySelector("path").setAttribute("d", d);
    // Color by direction
    var first = values[0], last = values[values.length - 1];
    var diff = last - first;
    if (diff > 0.01) {
      svgEl.classList.remove("flat", "down");
      svgEl.classList.add("up");
    } else if (diff < -0.01) {
      svgEl.classList.remove("flat", "up");
      svgEl.classList.add("down");
    } else {
      svgEl.classList.remove("up", "down");
      svgEl.classList.add("flat");
    }
    svgEl.title = "$" + first.toFixed(2) + " → $" + last.toFixed(2) + " (" + (diff >= 0 ? "+" : "") + (diff / first * 100).toFixed(1) + "%)";
  }

  /* ── Lazy sparkline load on row hover (avoids N API calls on render) ── */
  function wireTableRowClicks() {
    var tableBody = document.getElementById("inventoryBody");
    var debounceTimer = null;

    tableBody.addEventListener("mouseover", function (e) {
      var row = e.target.closest("tr");
      if (!row) return;
      var sparkCell = row.querySelector(".sparkline-cell");
      if (!sparkCell || sparkCell.dataset.loaded) return;
      var svg = sparkCell.querySelector("svg");
      if (!svg) return;
      var itemId = svg.getAttribute("data-sparkline-id");
      if (!itemId) return;

      // Debounce per cell
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        if (svg.dataset.loaded) return;
        fetchJSON("/api/comps/history?item_id=" + itemId + "&limit=60")
          .then(function (data) {
            renderSparkline(svg, data.snapshots || []);
            svg.dataset.loaded = "1";
          })
          .catch(function () {
            svg.classList.add("flat");
            svg.querySelector("path").setAttribute("d", "");
            svg.title = "Trend unavailable";
            svg.dataset.loaded = "1";
          });
      }, 180);
    });
  }

  /* ── Add / edit item modal ── */
  function wireAddItemModal() {
    var modal = document.getElementById("itemModal");
    var btn = document.getElementById("addItemBtn");
    var cancel = document.getElementById("modalCancelBtn");
    var form = document.getElementById("itemForm");

    btn.addEventListener("click", function () {
      document.getElementById("modalTitle").textContent = "Add Item";
      form.reset();
      document.getElementById("itemId").value = "";
      document.getElementById("itemTableType").value = currentTable;
      // Pre-fill search_query from item name (convenience)
      document.getElementById("itemSearchQuery").value = "";
      modal.hidden = false;
    });

    cancel.addEventListener("click", function () {
      modal.hidden = true;
    });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.hidden = true;
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var formData = new FormData(form);
      var rawId = formData.get("id");
      var payloadId = rawId && rawId !== "" ? parseInt(rawId, 10) : null;
      var payload = {
        id: payloadId,
        table_type: formData.get("table_type"),
        name: formData.get("name").trim(),
        category: formData.get("category").trim() || null,
        make: formData.get("make").trim() || null,
        model: formData.get("model").trim() || null,
        serial_number: formData.get("serial_number").trim() || null,
        condition: formData.get("condition").trim() || null,
        notes: formData.get("notes").trim() || null,
        acquisition_cost_cents: formData.get("acquisition_cost_cents") ? Math.round(parseFloat(formData.get("acquisition_cost_cents")) * 100) : null,
        buy_source: formData.get("buy_source").trim() || null,
        search_query: formData.get("search_query").trim() || null,
        metadata_json: null,
      };
      if (!payload.name) {
        toast("Name is required", "error");
        return;
      }
      var method = payload.id ? "POST" : "POST"; // upsert is POST for both create/update in our API
      fetchJSON("/api/inventory", {
        method: "POST",
        body: JSON.stringify(payload),
      })
        .then(function (data) {
          toast("Item saved", "success");
          modal.hidden = true;
          loadTable(currentTable, 0);
        })
        .catch(function (err) {
          toast("Save failed: " + err.message, "error");
        });
    });
  }

  /* ── Edit existing item ── */
  function wireEditItem(itemId) {
    var modal = document.getElementById("itemModal");
    var form = document.getElementById("itemForm");
    document.getElementById("modalTitle").textContent = "Edit Item";

    fetchJSON("/api/inventory/" + itemId)
      .then(function (data) {
        var item = data.item;
        document.getElementById("itemId").value = item.id || "";
        document.getElementById("itemTableType").value = item.table_type || "personal_assets";
        document.getElementById("itemName").value = item.name || "";
        document.getElementById("itemCategory").value = item.category || "";
        document.getElementById("itemMake").value = item.make || "";
        document.getElementById("itemModel").value = item.model || "";
        document.getElementById("itemSerial").value = item.serial_number || "";
        document.getElementById("itemCondition").value = item.condition || "";
        document.getElementById("itemBuyCost").value = item.acquisition_cost_cents ? (item.acquisition_cost_cents / 100).toFixed(2) : "";
        document.getElementById("itemBuySource").value = item.buy_source || "";
        document.getElementById("itemSearchQuery").value = item.search_query || "";
        document.getElementById("itemNotes").value = item.notes || "";
        modal.hidden = false;
      })
      .catch(function (err) {
        toast("Failed to load item: " + err.message, "error");
      });

    // Reuse the same save handler — it checks for id
  }

  /* ── Comp lookup modal ── */
  function wireRefreshComps() {
    document.getElementById("refreshCompsBtn").addEventListener("click", function () {
      // Find first checked item, or the first row if none checked
      var checked = document.querySelector(".row-checkbox:checked");
      var itemId = checked ? parseInt(checked.getAttribute("data-id"), 10) : null;
      if (!itemId) {
        var firstRow = document.querySelector("#inventoryBody tr");
        var firstComp = firstRow ? firstRow.querySelector(".lookup-comp-btn") : null;
        if (firstComp) {
          itemId = parseInt(firstComp.getAttribute("data-id"), 10);
        }
      }
      if (!itemId) {
        toast("No item selected. Select an item or check a row.", "error");
        return;
      }
      openCompModal(itemId);
    });
  }

  function openCompModal(itemId) {
    var modal = document.getElementById("compModal");
    document.getElementById("compItemId").value = itemId;
    document.getElementById("compQuery").value = "";
    document.getElementById("compLimit").value = 50;
    document.getElementById("compResult").hidden = true;
    modal.hidden = false;
  }

  function wireCompModal() {
    var modal = document.getElementById("compModal");
    var form = document.getElementById("compForm");
    var cancel = document.getElementById("compCancelBtn");

    cancel.addEventListener("click", function () {
      modal.hidden = true;
    });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.hidden = true;
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var formData = new FormData(form);
      var itemId = parseInt(formData.get("item_id"), 10);
      var query = formData.get("query").trim();
      var limit = parseInt(formData.get("limit"), 10) || 50;

      var btn = document.getElementById("compRunBtn");
      btn.disabled = true;
      btn.textContent = "Running…";

      // First load the item to get its default search_query if the user left the override blank
      fetchJSON("/api/inventory/" + itemId)
        .then(function (data) {
          var item = data.item;
          var ebayQuery = query || item.search_query || item.name;
          if (!ebayQuery) throw new Error("Item has no search query");
          return fetchJSON("/api/comps/lookup", {
            method: "POST",
            body: JSON.stringify({
              item_id: itemId,
              query: ebayQuery,
              limit: limit,
              table_type: item.table_type || "",
            }),
          });
        })
        .then(function (result) {
          showCompResult(itemId, result);
          toast("Comps refreshed", "success");
        })
        .catch(function (err) {
          toast("Comp lookup failed: " + err.message, "error");
        })
        .finally(function () {
          btn.disabled = false;
          btn.textContent = "Run Comps";
        });
    });
  }

  function showCompResult(itemId, result) {
    var summary = document.getElementById("compSummary");
    var itemsBox = document.getElementById("compItems");
    var activeAvg = result.active_avg || 0;
    var soldAvg = result.sold_avg || 0;

    summary.innerHTML =
      '<div class="comp-stat"><div class="comp-stat-label">Sold Avg</div><div class="comp-stat-value">' + fmtMoney(soldAvg) + '</div></div>' +
      '<div class="comp-stat"><div class="comp-stat-label">Active Avg</div><div class="comp-stat-value">' + fmtMoney(activeAvg) + '</div></div>' +
      '<div class="comp-stat"><div class="comp-stat-label">Sold Listings</div><div class="comp-stat-value">' + (result.sold_count || 0) + '</div></div>' +
      '<div class="comp-stat"><div class="comp-stat-label">Active Listings</div><div class="comp-stat-value">' + (result.active_count || 0) + '</div></div>';

    var compItems = result.sold_items.concat(result.active_items).slice(0, 12);
    itemsBox.innerHTML = compItems.map(function (ci) {
      var img = ci.image_url ? '<img src="' + escapeHtml(ci.image_url) + '" alt="" loading="lazy" />' : "";
      return '<div class="comp-item">' +
        img +
        '<div class="comp-item-title">' + escapeHtml(ci.title || "Unknown") + '</div>' +
        '<div class="comp-item-price">' + fmtMoney(ci.price) + '</div>' +
        '<div class="comp-item-condition">' + escapeHtml(ci.condition || "") + '</div>' +
        "</div>";
    }).join("");

    document.getElementById("compResult").hidden = false;
  }

  /* ── Batch comps ── */
  function wireBatchComps() {
    var modal = document.getElementById("batchModal");
    var cancel = document.getElementById("batchCancelBtn");
    var runBtn = document.getElementById("batchRunBtn");

    cancel.addEventListener("click", function () {
      modal.hidden = true;
    });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.hidden = true;
    });

    runBtn.addEventListener("click", function () {
      var checked = document.querySelectorAll(".row-checkbox:checked");
      var ids = [];
      checked.forEach(function (cb) {
        ids.push(parseInt(cb.getAttribute("data-id"), 10));
      });
      if (!ids.length) {
        toast("Select items to batch-comp using the row checkboxes.", "error");
        return;
      }
      modal.hidden = true;

      runBtn.disabled = true;
      runBtn.textContent = "Running batch…" ;

      fetchJSON("/api/comps/batch", {
        method: "POST",
        body: JSON.stringify({ item_ids: ids }),
      })
        .then(function (data) {
          var results = data.results || [];
          var ok = results.filter(function (r) { return r.ok; }).length;
          var failed = results.length - ok;
          toast("Batch complete: " + ok + " ok, " + failed + " failed", failed ? "error" : "success");
          loadTable(currentTable, 0);
          // Refresh memory market feed (batch writes it)
          if (currentTable === "ram_ssd_stockpile") loadMemoryMarket();
        })
        .catch(function (err) {
          toast("Batch failed: " + err.message, "error");
        })
        .finally(function () {
          runBtn.disabled = false;
          runBtn.textContent = "Run Batch";
        });
    });
  }

  /* ── Search / filter ── */
  function wireSearchFilter() {
    var searchInput = document.getElementById("itemSearch");
    var conditionFilter = document.getElementById("conditionFilter");
    var clearBtn = document.getElementById("clearFiltersBtn");

    function applyFilters() {
      var q = (searchInput.value || "").toLowerCase().trim();
      var cond = conditionFilter.value;
      var filtered = allItems.filter(function (item) {
        if (q) {
          var haystack = (item.name || "") + " " + (item.make || "") + " " + (item.model || "") + " " + (item.serial_number || "") + " " + (item.category || "");
          if (haystack.toLowerCase().indexOf(q) === -1) return false;
        }
        if (cond && item.condition !== cond) return false;
        return true;
      });
      // Re-render with filtered list, keeping current page where possible
      renderTableRows(filtered);
      renderStats(filtered);
      // Recalculate pager based on filtered set
      var maxPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1);
      if (currentPage > maxPage) currentPage = maxPage;
      updatePager(filtered.length);
    }

    searchInput.addEventListener("input", function () {
      // Debounce
      if (searchInput._timer) clearTimeout(searchInput._timer);
      searchInput._timer = setTimeout(applyFilters, 200);
    });
    conditionFilter.addEventListener("change", applyFilters);
    clearBtn.addEventListener("click", function () {
      searchInput.value = "";
      conditionFilter.value = "";
      applyFilters();
    });
  }

  /* ── Pager ── */
  function wirePager() {
    document.getElementById("prevPageBtn").addEventListener("click", function () {
      if (currentPage > 0) {
        currentPage--;
        loadTable(currentTable, currentPage);
      }
    });
    document.getElementById("nextPageBtn").addEventListener("click", function () {
      var maxPage = Math.max(0, Math.ceil(allItems.length / pageSize) - 1);
      if (currentPage < maxPage) {
        currentPage++;
        loadTable(currentTable, currentPage);
      }
    });
  }

  function updatePager(filteredLen) {
    var len = filteredLen !== undefined ? filteredLen : allItems.length;
    var maxPage = Math.max(0, Math.ceil(len / pageSize) - 1);
    var info = document.getElementById("pagerInfo");
    info.textContent = len > 0 ? ("Page " + (currentPage + 1) + " of " + (maxPage + 1)) : "No results";
    document.getElementById("prevPageBtn").disabled = (currentPage <= 0);
    document.getElementById("nextPageBtn").disabled = (currentPage >= maxPage);
  }

  /* ── Wire row action buttons (delegated) ── */
  function wireRowActions() {
    var tableBody = document.getElementById("inventoryBody");

    tableBody.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;

      if (btn.classList.contains("lookup-comp-btn")) {
        var id = parseInt(btn.getAttribute("data-id"), 10);
        openCompModal(id);
        return;
      }

      if (btn.classList.contains("edit-item-btn")) {
        var id = parseInt(btn.getAttribute("data-id"), 10);
        wireEditItem(id);
        return;
      }

      if (btn.classList.contains("log-event-btn")) {
        var id = parseInt(btn.getAttribute("data-id"), 10);
        logMemoryEvent(id);
        return;
      }
    });
  }

  /* ── Memory market sidebar ── */
  function loadMemoryMarket() {
    var genCards = document.getElementById("memoryGenCards");
    var eventsBox = document.getElementById("memoryEvents");

    genCards.innerHTML = '<div class="memory-gen-card shimmer" style="height:60px"></div>';
    eventsBox.innerHTML = '<div class="memory-event shimmer" style="height:40px"></div>';

    fetchJSON("/api/memory-market")
      .then(function (data) {
        renderMemoryGenCards(genCards, data.prices || []);
        renderMemoryEvents(eventsBox, data.events || []);
      })
      .catch(function () {
        genCards.innerHTML = '<div class="memory-gen-card"><div class="gen-label">DDR generation spot prices</div><div style="color:var(--text-muted);font-size:0.8rem">Unavailable</div></div>';
      });
  }

  function renderMemoryGenCards(container, prices) {
    var gens = ["DDR3", "DDR4", "DDR5"];
    var html = "";
    gens.forEach(function (gen) {
      var genPrices = prices.filter(function (p) { return p.generation === gen; });
      var avgSpot = null;
      if (genPrices.length) {
        var vals = genPrices.map(function (p) { return p.spot_avg_cents || 0; }).filter(Boolean);
        avgSpot = vals.length ? vals.reduce(function (a, b) { return a + b; }, 0) / vals.length : null;
      }
      var changeHtml = "";
      if (genPrices.length >= 5) {
        var recent = genPrices.slice(0, 5).map(function (p) { return p.spot_avg_cents || 0; });
        var first = recent[recent.length - 1] || 0;
        var last = recent[0] || 0;
        var diff = last - first;
        if (first) {
          var pct = (diff / first * 100);
          changeHtml = '<div class="gen-change ' + (pct >= 0 ? "up" : "down") + '">' +
            (pct >= 0 ? "+" : "") + pct.toFixed(1) + "% (5-period)</div>";
        }
      }
      html += '<div class="memory-gen-card">' +
        '<div class="gen-label">' + gen + '</div>' +
        '<div class="gen-value">' + (avgSpot ? "$" + (avgSpot / 100).toFixed(2) : "—") + '</div>' +
        changeHtml +
        '</div>';
    });
    container.innerHTML = html;
  }

  function renderMemoryEvents(container, events) {
    if (!events.length) {
      container.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem">No supply-side events logged yet.</div>';
      return;
    }
    container.innerHTML = events.map(function (ev) {
      return '<div class="memory-event">' +
        '<span class="event-type ' + (ev.event_type || "") + '">' + (ev.event_type || "event") + '</span>' +
        '<span class="event-headline">' + escapeHtml(ev.headline || "") + '</span>' +
        (ev.body ? '<div style="color:var(--text-muted);font-size:0.75rem;margin-top:0.15rem">' + escapeHtml(ev.body) + '</div>' : "") +
        (ev.observed_at ? '<span class="event-date">' + formatDate(ev.observed_at) + '</span>' : "") +
        "</div>";
    }).join("");
  }

  function logMemoryEvent(itemId) {
    // Quick inline log: prompt for event type + headline, write to memory_events.
    var eventType = prompt("Event type (tariff, fab_capacity, price_spike, price_drop):", "price_spike");
    if (!eventType) return;
    var headline = prompt("Short headline:", "DDR4 spot up");
    if (!headline) return;
    var body = prompt("Optional detail:", "");
    fetchJSON("/api/memory-market", {
      method: "POST",
      body: JSON.stringify({
        event_type: eventType,
        generation: "DDR4",
        headline: headline,
        body: body || null,
      }),
    })
      .then(function () {
        toast("Event logged", "success");
        loadMemoryMarket();
      })
      .catch(function (err) {
        toast("Failed to log event: " + err.message, "error");
      });
  }

  /* ── Utilities ── */
  function fmtMoney(dollars) {
    if (dollars === null || dollars === undefined || isNaN(dollars)) return "—";
    return "$" + dollars.toFixed(2);
  }

  function formatDate(iso) {
    if (!iso) return "—";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      var now = new Date();
      var diff = now - d;
      if (diff < 60000) return "just now";
      if (diff < 3600000) return Math.floor(diff / 60000) + "m ago";
      if (diff < 86400000) return Math.floor(diff / 3600000) + "h ago";
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    } catch (e) {
      return iso;
    }
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Wire the delegated row-action clicks after the table renderer is defined.
  // (Called from DOMContentLoaded via wireTableRowClicks already above.)
  // Additional init: wire the modal buttons that were defined as separate functions.
  document.addEventListener("DOMContentLoaded", function () {
    wireCompModal();
    wireRowActions();
  });
})();
