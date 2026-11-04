/* Popup del merge con Apollo: Validar de Santander / GP / La Anónima se frena cuando Apollo tiene
   facturas dudosas para sumar a la Sheet (conflicto, superposición, cuenta que la Sheet no tiene,
   dato sin columna, valor fuera de lo habitual…). Por cada una: agregar al merge, quitarla,
   corregir la línea de la Sheet en el CSV, o corregirla en Apollo y mergearla.
   MergeModal.open(payload, {selectedId, onApplied(lines), onCancel()}) */
(function () {
    const ACTIONS = {
        add: "Agregar al merge",
        skip: "Quitar del merge",
        edit_csv: "Modificar la línea del CSV (Sheet)",
        edit_apollo: "Modificar en Apollo y mergear",
    };
    const BLOCKING = new Set(["same_number", "same_period", "overlap", "no_account", "apollo_overlap"]);
    const TAG = {
        same_number: "Mismo número", same_period: "Mismo período", overlap: "Se superpone",
        no_account: "Cuenta", dropped: "Sin columna", outlier: "Valor raro", apollo_overlap: "Duplicada en Apollo",
    };
    const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

    let root, state = null;

    function build() {
        if (root) return;
        root = document.createElement("div");
        root.className = "mm";
        root.hidden = true;
        root.innerHTML = `
          <div class="mm-card" role="dialog" aria-modal="true" aria-labelledby="mmTitle">
            <div class="mm-head">
              <h3 id="mmTitle">Facturas de Apollo para revisar</h3>
              <div class="mm-sub" id="mmSub"></div>
              <div class="mm-msg" id="mmMsg" hidden></div>
              <div class="mm-bulk">Para todas:
                <button type="button" class="mm-ghost mm-small" data-bulk="skip">Quitar del merge</button>
                <button type="button" class="mm-ghost mm-small" data-bulk="add">Agregar al merge</button>
                <button type="button" class="mm-ghost mm-small" data-bulk="default">Lo sugerido</button>
              </div>
            </div>
            <div class="mm-body" id="mmBody"></div>
            <div class="mm-foot">
              <span class="mm-count" id="mmCount"></span>
              <button type="button" class="mm-ghost" id="mmCancel">Cancelar validación</button>
              <button type="button" id="mmApply">Aplicar y seguir validando</button>
            </div>
          </div>`;
        document.body.appendChild(root);
        root.querySelector("#mmCancel").onclick = () => { close(); state?.opts.onCancel?.(); };
        root.querySelector("#mmApply").onclick = submit;
        root.querySelectorAll("[data-bulk]").forEach(b => b.onclick = () => {
            state.items.forEach(c => setAction(c, b.dataset.bulk === "default" ? c.default : b.dataset.bulk));
        });
        document.addEventListener("keydown", e => {
            if (e.key === "Escape" && !root.hidden) { close(); state?.opts.onCancel?.(); }
        });
    }

    function close() { root.hidden = true; document.body.style.overflow = ""; }

    function setAction(c, action) {
        if (!c.actions.includes(action)) return;
        const el = root.querySelector(`.mm-item[data-id="${CSS.escape(c.id)}"]`);
        const radio = el?.querySelector(`input[type=radio][value="${action}"]`);
        if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change")); }
    }

    function diffTable(c) {
        if (c.twin && c.diff.length) {
            return `<table class="mm-table"><tr><th>Columna</th><th>Apollo</th><th>Sheet (línea ${c.twin.line})</th></tr>
                ${c.diff.map(([col, a, s]) => `<tr><td class="col">${esc(col)}</td><td class="a">${esc(a) || "—"}</td><td class="s">${esc(s) || "—"}</td></tr>`).join("")}</table>`;
        }
        const vals = Object.entries(c.apollo).filter(([, v]) => String(v).trim());
        return `<table class="mm-table"><tr><th>Columna</th><th>Apollo</th></tr>
            ${vals.map(([col, v]) => `<tr><td class="col">${esc(col)}</td><td class="a">${esc(v)}</td></tr>`).join("")}</table>`;
    }

    function csvEditor(c, header) {
        const row = c.twin.row, differs = new Set(c.diff.map(d => d[0]));
        const cols = header.filter(h => differs.has(h)).concat(header.filter(h => !differs.has(h)));
        return `<div class="mm-edit" data-edit="edit_csv" hidden>
            <div class="mm-edit-note">Línea ${c.twin.line} del CSV de la Sheet (${esc(c.table)}.csv). Decimales con punto, fechas AAAA-MM-DD.
              Corregilo también en la Sheet; si no, vuelve en la próxima importación. La factura de Apollo no se agrega.
              <button type="button" class="mm-ghost mm-small" data-copy="apollo">Copiar los valores de Apollo</button></div>
            <div class="mm-grid">${cols.map(h => `<label>${esc(h)}<input data-col="${esc(h)}" data-orig="${esc(row[h])}"
                class="${differs.has(h) ? "differs" : ""}" value="${esc(row[h])}"></label>`).join("")}</div></div>`;
    }

    function apolloEditor(c) {
        const f = c.apollo_edit?.fields || [];
        return `<div class="mm-edit" data-edit="edit_apollo" hidden>
            <div class="mm-edit-note">Se corrige la factura en Apollo (queda en Auditorías como edición del merge) y después se agrega a la Sheet.
              Fechas DD/MM/AAAA, números como en el panel de carga.</div>
            <div class="mm-grid">${f.map(x => `<label>${esc(x.label)}<input data-field="${esc(x.field)}" data-orig="${esc(x.value)}" value="${esc(x.value)}"></label>`).join("")}</div></div>`;
    }

    function render() {
        const an = state.analysis, items = state.items;
        root.querySelector("#mmSub").textContent =
            `${state.folder} · ${items.length} para revisar` +
            (an.ok ? ` · ${an.ok} sin observaciones se agregan solas` : "") +
            (an.already_in_sheet ? ` · ${an.already_in_sheet} ya estaban en la Sheet tal cual` : "");
        const msg = root.querySelector("#mmMsg");
        msg.hidden = !state.message;
        msg.className = "mm-msg" + (state.error ? " err" : "");
        msg.textContent = state.message || "";
        const body = root.querySelector("#mmBody");
        body.innerHTML = items.map(c => `
          <div class="mm-item" data-id="${esc(c.id)}">
            <div class="mm-title">${esc(c.label)}</div>
            <ul class="mm-issues">${c.issues.map(i => `<li><span class="mm-tag ${BLOCKING.has(i.kind) ? "block" : "info"}">${esc(TAG[i.kind] || i.kind)}</span><span>${esc(i.text)}</span></li>`).join("")}</ul>
            ${c.remembered ? `<div class="mm-remember">La última vez que apareció igual elegiste: ${esc(ACTIONS[c.default])}</div>` : ""}
            ${diffTable(c)}
            <div class="mm-actions">${c.actions.map(a => `<label><input type="radio" name="mm-${esc(c.id)}" value="${a}" ${a === c.default ? "checked" : ""}> ${ACTIONS[a]}</label>`).join("")}</div>
            ${c.actions.includes("edit_csv") ? csvEditor(c, an.headers[c.table] || Object.keys(c.apollo)) : ""}
            ${c.actions.includes("edit_apollo") ? apolloEditor(c) : ""}
          </div>`).join("");
        items.forEach(c => {
            const el = body.querySelector(`.mm-item[data-id="${CSS.escape(c.id)}"]`);
            const sync = () => {
                const v = el.querySelector("input[type=radio]:checked")?.value;
                el.querySelectorAll(".mm-edit").forEach(e => e.hidden = e.dataset.edit !== v);
                el.classList.toggle("decided", !!v);
                count();
            };
            el.querySelectorAll("input[type=radio]").forEach(r => r.onchange = sync);
            el.querySelectorAll(".mm-grid input").forEach(i => i.oninput = () => i.classList.toggle("changed", i.value !== i.dataset.orig));
            const copy = el.querySelector("[data-copy=apollo]");
            if (copy) copy.onclick = () => el.querySelectorAll('[data-edit=edit_csv] input[data-col]').forEach(i => {
                const v = c.apollo[i.dataset.col];
                if (v !== undefined && v !== i.value) { i.value = v; i.dispatchEvent(new Event("input")); }
            });
            sync();
        });
        body.scrollTop = 0;
    }

    function count() {
        const n = state.items.filter(c => root.querySelector(`.mm-item[data-id="${CSS.escape(c.id)}"] input[type=radio]:checked`)).length;
        root.querySelector("#mmCount").textContent = `${n} de ${state.items.length} decididas`;
    }

    function collect() {
        const decisions = {};
        for (const c of state.items) {
            const el = root.querySelector(`.mm-item[data-id="${CSS.escape(c.id)}"]`);
            const action = el.querySelector("input[type=radio]:checked")?.value;
            if (!action) return { missing: c };
            const d = { action };
            if (action === "edit_csv" || action === "edit_apollo") {
                const values = {};
                el.querySelectorAll(`[data-edit=${action}] input`).forEach(i => {
                    if (i.value !== i.dataset.orig) values[i.dataset.col || i.dataset.field] = i.value;
                });
                if (!Object.keys(values).length) return { missing: c, why: "no cambiaste ningún valor" };
                d.values = values;
            }
            decisions[c.id] = d;
        }
        return { decisions };
    }

    async function submit() {
        const { decisions, missing, why } = collect();
        if (missing) {
            state.message = `${missing.label}: ${why || "elegí qué hacer"}`;
            state.error = true;
            root.querySelector("#mmMsg").hidden = false;
            root.querySelector("#mmMsg").className = "mm-msg err";
            root.querySelector("#mmMsg").textContent = state.message;
            root.querySelector(`.mm-item[data-id="${CSS.escape(missing.id)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
            return;
        }
        const btn = root.querySelector("#mmApply");
        btn.disabled = true; btn.textContent = "Aplicando…";
        try {
            const res = await fetch("/merge-apollo/apply", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ selectedId: state.opts.selectedId, folder: state.folder, decisions }),
            });
            if (res.status === 401) { window.location.href = "/login"; return; }
            const d = await res.json().catch(() => ({}));
            if (d.status === "ok") {
                close();
                state.opts.onApplied?.(d.lines || []);
            } else if (d.status === "needs_decision" && d.analysis) {
                load(d.analysis, d.message, true);
            } else {
                state.message = d.message || `Error ${res.status}`;
                state.error = true;
                render();
            }
        } catch (e) {
            state.message = "No se pudo aplicar: " + e.message;
            state.error = true;
            render();
        } finally {
            btn.disabled = false; btn.textContent = "Aplicar y seguir validando";
        }
    }

    function load(analysis, message, error) {
        state.analysis = analysis;
        state.items = (analysis.candidates || []).filter(c => c.status === "issue");
        state.message = message || "";
        state.error = !!error;
        render();
    }

    // "Facturas desde Apollo": what the merge did with each invoice Apollo has and the Sheet lacked
    const ESTADO_CLASS = { add: "ok", in_sheet: "same", edit_apollo_same: "same", add_warn: "warn",
                           edit_csv: "warn", edit_apollo: "warn", skip: "off" };

    function renderReport(container, report) {
        if (!container) return;
        const inv = report.invoices || [];
        const n = k => inv.filter(i => i.estado === k).length;
        const added = Object.values(report.added || {}).reduce((a, b) => a + b, 0);
        container.hidden = false;
        container.innerHTML = `
          <div class="err-reports-head"><span class="err-reports-title">Facturas desde Apollo</span></div>
          <div class="mr-summary">
            <span class="mr-chip ok">${added} agregada(s)</span>
            ${n("add_warn") ? `<span class="mr-chip warn">${n("add_warn")} con observaciones</span>` : ""}
            ${n("edit_csv") ? `<span class="mr-chip warn">${n("edit_csv")} línea(s) de la Sheet corregidas</span>` : ""}
            ${n("edit_apollo") + n("edit_apollo_same") ? `<span class="mr-chip warn">${n("edit_apollo") + n("edit_apollo_same")} corregida(s) en Apollo</span>` : ""}
            ${n("skip") ? `<span class="mr-chip off">${n("skip")} quitada(s)</span>` : ""}
            ${n("in_sheet") ? `<span class="mr-chip same">${n("in_sheet")} ya estaban en la Sheet</span>` : ""}
            <span class="mr-meta">${esc(report.folder || "")}${report.usuario ? " · " + esc(report.usuario) : ""}${report.merged_at ? " · " + esc(report.merged_at.replace("T", " ")) : ""}</span>
          </div>
          ${inv.length ? `<table class="metrics-diff-table mr-table"><thead><tr>
              <th>Estado</th><th>Servicio</th><th>Distribuidora</th><th>Cuenta</th><th>Sitio</th><th>Período</th><th>N° factura</th>
              <th class="num">Consumo</th><th class="num">Costo neto</th><th>Observaciones</th></tr></thead><tbody>
            ${inv.map(i => `<tr>
              <td><span class="mr-chip ${ESTADO_CLASS[i.estado] || "same"}">${esc(i.estado_texto)}</span></td>
              <td>${esc(i.servicio)}</td><td>${esc(i.proveedor)}</td><td>${esc(i.cuenta)}</td><td>${esc(i.id_sitio)}</td>
              <td>${esc(i.fecha_anterior)} → ${esc(i.fecha_actual)}</td><td>${esc(i.num_factura)}</td>
              <td class="num">${esc(i.consumo)}</td><td class="num">${esc(i.costo_neto)}</td>
              <td class="mr-obs">${(i.observaciones || []).map(esc).join("<br>") || (i.estado === "add" ? "Sin observaciones" : "")}${
                  i.cambios_sheet ? `<br>Línea ${i.linea_sheet}: ${Object.entries(i.cambios_sheet).map(([c, [a, b]]) => `${esc(c)} ${esc(a) || "—"} → ${esc(b) || "—"}`).join(", ")}` : ""}</td>
            </tr>`).join("")}</tbody></table>`
            : `<div class="err-reports-empty">${added ? "Detalle por factura no disponible para este merge." : "Apollo no tiene facturas que falten en la Sheet."}</div>`}`;
    }

    window.MergeModal = {
        renderReport,
        open(payload, opts) {
            build();
            state = { folder: payload.folder, opts: opts || {} };
            load(payload.analysis || {}, payload.message, false);
            root.hidden = false;
            document.body.style.overflow = "hidden";
        },
    };
})();
