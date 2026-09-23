import { LitElement, css, html, type PropertyValues } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import "./components/task-lists";
import "./components/task-item";
import "./components/task-form";
import "./components/create-list-dialog";
import "./components/edit-list-dialog";
import "./components/edit-task-dialog";
import "./components/snooze-dialog";

import type { HomeAssistant, UnsubscribeFunc } from "./ha";
import {
  HomeUpkeepApi,
  type HomeUpkeepEvent,
  type ImportDoc,
  type Task,
  type TaskCreate,
  type TaskList,
  type TaskUpdate,
} from "./ha-api";
import { parseDueDate } from "./dates";
import {
  bannerStyles,
  burgerStyles,
  buttonStyles,
  designTokens,
  sectionStyles,
  spinnerStyles,
  taskListStyles,
  toolbarStyles,
} from "./styles";

const LAST_LIST_STORAGE_KEY = "home-upkeep-last-list-id";

function readStoredListId(): number | undefined {
  const raw = localStorage.getItem(LAST_LIST_STORAGE_KEY);
  const parsed = raw ? Number(raw) : NaN;
  return Number.isFinite(parsed) ? parsed : undefined;
}

function writeStoredListId(id: number | undefined): void {
  if (id == null) {
    localStorage.removeItem(LAST_LIST_STORAGE_KEY);
  } else {
    localStorage.setItem(LAST_LIST_STORAGE_KEY, String(id));
  }
}

function errorMessage(err: unknown): string {
  if (err && typeof err === "object" && "message" in err) {
    return String((err as { message: unknown }).message);
  }
  return String(err);
}

function startOfDay(d: Date): number {
  const c = new Date(d);
  c.setHours(0, 0, 0, 0);
  return c.getTime();
}

/** Panel root element, registered by `panel.py` via `panel_custom`. */
@customElement("home-upkeep-panel")
export class HomeUpkeepPanel extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ type: Boolean }) narrow = false;

  @property({ attribute: false }) route: unknown;

  @property({ attribute: false }) panel: unknown;

  @state() private _lists: TaskList[] = [];

  @state() private _tasks: Task[] = [];

  @state() private _selectedListId: number | undefined;

  @state() private _loading = false;

  @state() private _error: string | null = null;

  @state() private _migratedFromAddon = false;

  @state() private _addonRunning = false;

  @state() private _addonBannerDismissed = false;

  @state() private _mobileMenuOpen = false;

  @state() private _showCreateFormMobile = false;

  @state() private _creatingList = false;

  @state() private _editingList: TaskList | null = null;

  @state() private _editingTask: Task | null = null;

  @state() private _snoozingTask: Task | null = null;

  private _api?: HomeUpkeepApi;

  private _unsubscribe?: UnsubscribeFunc;

  private _taskRefreshSeq = 0;

  static styles = [
    designTokens,
    toolbarStyles,
    burgerStyles,
    buttonStyles,
    sectionStyles,
    taskListStyles,
    bannerStyles,
    spinnerStyles,
    css`
      :host {
        display: block;
        min-height: 100vh;
        background: var(--hu-bg);
      }
      .page {
        max-width: 80rem;
        margin: 0 auto;
        padding: 2rem 1rem;
        box-sizing: border-box;
      }
      .page-narrow {
        padding-block: 0;
      }
      @media (min-width: 640px) {
        .page {
          padding-inline: 1.5rem;
        }
      }
      @media (min-width: 1024px) {
        .page {
          padding-inline: 2rem;
        }
      }
      .layout {
        display: grid;
        grid-template-columns: 1fr;
        gap: 2rem;
      }
      @media (min-width: 1024px) {
        .layout {
          grid-template-columns: repeat(4, 1fr);
        }
      }
      main {
        grid-column: span 1 / span 1;
      }
      @media (min-width: 1024px) {
        main {
          grid-column: span 3 / span 3;
        }
      }
      .header {
        margin-bottom: 2rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      h1 {
        margin: 0 0 0.5rem;
        font-size: 1.875rem;
        font-weight: 700;
        color: var(--hu-text);
      }
      .subtitle {
        margin: 0;
        color: var(--hu-text-muted);
      }
      .form-area {
        margin-bottom: 1.5rem;
      }
      .form-area-mobile {
        display: block;
      }
      @media (min-width: 1024px) {
        .form-area-mobile {
          display: none;
        }
      }
      .form-area-desktop {
        display: none;
      }
      @media (min-width: 1024px) {
        .form-area-desktop {
          display: block;
        }
      }
      .sections {
        display: flex;
        flex-direction: column;
        gap: 2rem;
      }
      section.first {
        margin-top: 1.5rem;
      }
    `,
  ];

  connectedCallback(): void {
    super.connectedCallback();
    if (this.hass && !this._api) {
      this._init();
    }
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this._unsubscribe?.();
    this._unsubscribe = undefined;
    this._api = undefined;
  }

  willUpdate(changed: PropertyValues): void {
    if (changed.has("hass") && this.hass && !this._api) {
      this._init();
    }
    if (
      changed.has("_lists") &&
      this._lists.length &&
      this._selectedListId == null
    ) {
      const lastListId = readStoredListId();
      this._selectedListId = this._lists.some((l) => l.id === lastListId)
        ? lastListId
        : this._lists[0]?.id;
    }
    if (
      changed.has("_selectedListId") &&
      changed.get("_selectedListId") !== this._selectedListId
    ) {
      this._refreshTasks();
      writeStoredListId(this._selectedListId);
    }
  }

  private async _init(): Promise<void> {
    this._api = new HomeUpkeepApi(this.hass);
    await this._refreshLists();
    await this._refreshMigrationStatus();
    this._unsubscribe = await this._api.subscribe((event) => {
      this._handleEvent(event);
    });
  }

  private async _refreshMigrationStatus(): Promise<void> {
    try {
      const status = await this._api!.getMigrationStatus();
      this._migratedFromAddon = status.migrated_from_addon;
      this._addonRunning = status.addon_running;
    } catch (err) {
      console.error(err);
    }
  }

  private async _refreshLists(): Promise<void> {
    try {
      this._lists = await this._api!.listLists();
    } catch (err) {
      this._error = errorMessage(err);
    }
  }

  private async _refreshTasks(): Promise<void> {
    const seq = ++this._taskRefreshSeq;
    if (this._selectedListId == null) {
      this._tasks = [];
      this._loading = false;
      this._error = null;
      return;
    }
    this._loading = true;
    this._error = null;
    try {
      const tasks = await this._api!.listTasks(this._selectedListId);
      // A newer refresh (e.g. from switching lists again) may have
      // started and resolved while this one was in flight; discard this
      // stale response rather than overwriting the current one.
      if (seq !== this._taskRefreshSeq) return;
      this._tasks = tasks;
    } catch (err) {
      if (seq !== this._taskRefreshSeq) return;
      this._error = errorMessage(err);
    } finally {
      if (seq === this._taskRefreshSeq) this._loading = false;
    }
  }

  private _handleEvent(event: HomeUpkeepEvent): void {
    switch (event.type) {
      case "list_created":
        if (event.list) this._lists = [...this._lists, event.list];
        break;
      case "list_updated":
        if (event.list) {
          const updated = event.list;
          this._lists = this._lists.map((l) =>
            l.id === updated.id ? updated : l,
          );
        }
        break;
      case "list_deleted":
        if (event.list_id != null) {
          this._lists = this._lists.filter((l) => l.id !== event.list_id);
        }
        break;
      case "task_created":
        if (event.task && event.list_id === this._selectedListId) {
          this._tasks = [event.task, ...this._tasks];
        }
        break;
      case "task_updated": {
        if (!event.task) break;
        const updated = event.task;
        // event.list_id is the task's *new* list_id, so a task moved out
        // of the selected list must be removed rather than left stale,
        // and one moved into it must be added rather than silently
        // dropped by a no-op map over an id it doesn't contain yet.
        if (event.list_id === this._selectedListId) {
          this._tasks = this._tasks.some((t) => t.id === updated.id)
            ? this._tasks.map((t) => (t.id === updated.id ? updated : t))
            : [updated, ...this._tasks];
        } else {
          this._tasks = this._tasks.filter((t) => t.id !== updated.id);
        }
        break;
      }
      case "task_deleted":
        if (event.task_id != null && event.list_id === this._selectedListId) {
          this._tasks = this._tasks.filter((t) => t.id !== event.task_id);
        }
        break;
      case "migrated_from_addon":
        this._migratedFromAddon = event.migrated_from_addon ?? true;
        break;
      case "data_imported":
        // An import (possibly from another connected client) may have
        // added/overwritten lists and the currently-selected list's tasks;
        // there's no per-list detail on this event, so just refresh both.
        this._refreshLists();
        this._refreshTasks();
        break;
      default:
        break;
    }
  }

  private _toggleMobileMenu(): void {
    this._mobileMenuOpen = !this._mobileMenuOpen;
  }

  /**
   * Ask the Home Assistant frontend shell to open its sidebar/drawer.
   *
   * Core panels get this for free from `hass-tabs-subpage`/`ha-menu-button`;
   * ours is a bare custom element registered via `panel_custom` with
   * `embed_iframe: false` (see `panel.py`), so nothing renders the standard
   * menu button automatically. Without it, narrow layouts — most visibly
   * the iOS/Android companion apps, which are always narrow — have no way
   * back to the rest of Home Assistant once inside this panel.
   */
  private _toggleHassMenu(): void {
    this.dispatchEvent(
      new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }),
    );
  }

  private async _createTask(payload: TaskCreate): Promise<void> {
    await this._api!.createTask(payload);
  }

  private async _toggleTask(task: Task): Promise<void> {
    try {
      await this._api!.updateTask(task.id, { completed: !task.completed });
    } catch (err) {
      console.error(err);
    }
  }

  private async _deleteTask(task: Task): Promise<void> {
    try {
      await this._api!.deleteTask(task.id);
    } catch (err) {
      console.error(err);
    }
  }

  private async _saveEditedTask(payload: TaskUpdate): Promise<void> {
    if (!this._editingTask) return;
    await this._api!.updateTask(this._editingTask.id, payload);
    this._editingTask = null;
  }

  private async _saveSnooze(period: string): Promise<void> {
    if (!this._snoozingTask) return;
    try {
      await this._api!.snoozeTask(this._snoozingTask.id, period);
    } catch (err) {
      console.error(err);
    }
    this._snoozingTask = null;
  }

  private async _createList(name: string): Promise<void> {
    const created = await this._api!.createList(name);
    this._selectedListId = created.id;
    this._creatingList = false;
  }

  private async _saveEditedList(name: string): Promise<void> {
    if (!this._editingList) return;
    await this._api!.renameList(this._editingList.id, name);
    this._editingList = null;
  }

  private async _importDocs(docs: ImportDoc[]): Promise<void> {
    try {
      let result = await this._api!.importJson(docs);
      if (!result.imported) {
        const names = result.conflicts.map((c) => c.name).join(", ");
        const overwrite = confirm(
          `${names} already exist(s). Overwrite with the imported data? ` +
            "This replaces their tasks too.",
        );
        if (!overwrite) return;
        result = await this._api!.importJson(
          docs,
          result.conflicts.map((c) => c.id),
        );
      }
      await this._refreshLists();
      // The import may have replaced the currently-selected list's tasks
      // entirely; refresh them too so the view isn't left showing stale
      // pre-import data.
      await this._refreshTasks();
      alert(
        `Imported ${result.list_count} list(s) and ${result.task_count} task(s).`,
      );
    } catch (err) {
      alert(`Import failed: ${errorMessage(err)}`);
    }
  }

  private async _deleteList(id: number): Promise<void> {
    const list = this._lists.find((l) => l.id === id);
    if (!list) return;
    const ok = confirm(
      `Delete list "${list.name}"? This removes its tasks too.`,
    );
    if (!ok) return;
    try {
      await this._api!.deleteList(id);
    } catch (err) {
      console.error(err);
      return;
    }
    // Read `this._lists` fresh rather than a pre-await snapshot: the
    // `list_deleted` event for this very call may already have arrived
    // and updated it (dispatcher fires before the WS result — see
    // CLAUDE.md's ordering-quirk note), and another client may have
    // mutated the list set concurrently.
    if (this._selectedListId === id) {
      const remaining = this._lists.filter((x) => x.id !== id);
      this._selectedListId = remaining.length ? remaining[0]?.id : undefined;
    }
  }

  private _isDueOrOverdue(t: Task, today: number): boolean {
    if (t.completed) return false;
    if (!t.due_date) return true;
    return startOfDay(parseDueDate(t.due_date)) <= today;
  }

  private _renderTaskItem(t: Task) {
    return html`<home-upkeep-task-item
      .task=${t}
      @task-toggle=${() => this._toggleTask(t)}
      @task-delete=${() => this._deleteTask(t)}
      @task-edit=${() => {
        this._editingTask = t;
      }}
      @task-snooze=${() => {
        this._snoozingTask = t;
      }}
    ></home-upkeep-task-item>`;
  }

  private _renderTaskSections() {
    const today = startOfDay(new Date());

    const due = this._tasks
      .filter((t) => this._isDueOrOverdue(t, today))
      .sort((a, b) => {
        const ad = a.due_date
          ? startOfDay(parseDueDate(a.due_date))
          : Infinity;
        const bd = b.due_date
          ? startOfDay(parseDueDate(b.due_date))
          : Infinity;
        if (ad !== bd) return ad - bd;
        return (
          new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
        );
      });

    const upcoming = this._tasks
      .filter(
        (t) =>
          !t.completed &&
          t.due_date &&
          startOfDay(parseDueDate(t.due_date)) > today,
      )
      .sort((a, b) => {
        const ad = startOfDay(parseDueDate(a.due_date!));
        const bd = startOfDay(parseDueDate(b.due_date!));
        if (ad !== bd) return ad - bd;
        return (
          new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
        );
      });

    const completed = this._tasks
      .filter((t) => t.completed)
      .sort((a, b) => {
        const at = a.completed_at ? new Date(a.completed_at).getTime() : 0;
        const bt = b.completed_at ? new Date(b.completed_at).getTime() : 0;
        return bt - at;
      });

    return html`
      <div class="sections">
        <section class="first">
          <div class="section-header">
            <h2 class="section-title">Due / Overdue</h2>
            <span class="count-due">${due.length}</span>
          </div>
          <div class="task-list">
            ${due.map((t) => this._renderTaskItem(t))}
            ${due.length === 0
              ? html`<div class="empty-state">
                  <p class="empty-state-text">Nothing due right now</p>
                </div>`
              : null}
          </div>
        </section>

        <section>
          <div class="section-header">
            <h2 class="section-title">Upcoming</h2>
            <span class="count-upcoming">${upcoming.length}</span>
          </div>
          <div class="task-list">
            ${upcoming.map((t) => this._renderTaskItem(t))}
            ${upcoming.length === 0
              ? html`<div class="empty-state">
                  <p class="empty-state-text">No upcoming tasks</p>
                </div>`
              : null}
          </div>
        </section>

        <section>
          <div class="section-header">
            <h2 class="section-title">Completed</h2>
            <span class="count-completed">${completed.length}</span>
          </div>
          <div class="task-list">
            ${completed.map((t) => this._renderTaskItem(t))}
            ${completed.length === 0
              ? html`<div class="empty-state">
                  <p class="empty-state-text">No completed tasks yet</p>
                </div>`
              : null}
          </div>
        </section>
      </div>
    `;
  }

  render() {
    const selectedList = this._lists.find(
      (l) => l.id === this._selectedListId,
    );

    return html`
      ${this.narrow
        ? html`<div class="app-toolbar">
            <button
              class="hass-menu-button"
              aria-label="Open menu"
              @click=${() => this._toggleHassMenu()}
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                <path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z" />
              </svg>
            </button>
            <span class="app-toolbar-title">Home Upkeep</span>
          </div>`
        : null}
      <div class="page${this.narrow ? " page-narrow" : ""}">
        ${this._migratedFromAddon &&
        this._addonRunning &&
        !this._addonBannerDismissed
          ? html`<div class="banner banner-dismissible">
              <div class="banner-title">
                Home Upkeep add-on detected — its data has been migrated to
                this panel. You can uninstall the add-on now.
              </div>
              <button
                class="banner-dismiss"
                aria-label="Dismiss"
                @click=${() => {
                  this._addonBannerDismissed = true;
                }}
              >
                ✕
              </button>
            </div>`
          : null}
        <div class="layout">
          <home-upkeep-task-lists
            .lists=${this._lists}
            .selectedListId=${this._selectedListId}
            .mobileMenuOpen=${this._mobileMenuOpen}
            @list-select=${(e: CustomEvent<{ id: number }>) => {
              this._selectedListId = e.detail.id;
            }}
            @list-create=${() => {
              this._creatingList = true;
            }}
            @list-import=${(e: CustomEvent<{ docs: ImportDoc[] }>) =>
              this._importDocs(e.detail.docs)}
            @list-edit=${(e: CustomEvent<{ list: TaskList }>) => {
              this._editingList = e.detail.list;
            }}
            @list-delete=${(e: CustomEvent<{ id: number }>) => {
              this._deleteList(e.detail.id);
            }}
            @mobile-menu-toggle=${() => this._toggleMobileMenu()}
          ></home-upkeep-task-lists>

          <main>
            <div class="header">
              <div>
                <h1>${selectedList ? selectedList.name : "Home Upkeep"}</h1>
                ${selectedList
                  ? null
                  : html`<p class="subtitle">
                      Select a list to get started
                    </p>`}
              </div>
              <button
                class="burger"
                aria-label="Toggle menu"
                @click=${() => this._toggleMobileMenu()}
              >
                <svg
                  width="24"
                  height="24"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M4 6h16M4 12h16M4 18h16"
                  />
                </svg>
              </button>
            </div>

            ${this._selectedListId != null
              ? html`
                  <div class="form-area">
                    <div class="form-area-mobile">
                      ${!this._showCreateFormMobile
                        ? html`<button
                            class="btn-primary"
                            style="width: 100%;"
                            @click=${() => {
                              this._showCreateFormMobile = true;
                            }}
                          >
                            Add Task
                          </button>`
                        : html`<home-upkeep-task-form
                            .listId=${this._selectedListId}
                            .onSubmit=${async (payload: TaskCreate) => {
                              await this._createTask(payload);
                              this._showCreateFormMobile = false;
                            }}
                            .onCancel=${() => {
                              this._showCreateFormMobile = false;
                            }}
                          ></home-upkeep-task-form>`}
                    </div>
                    <div class="form-area-desktop">
                      <home-upkeep-task-form
                        .listId=${this._selectedListId}
                        .onSubmit=${(payload: TaskCreate) =>
                          this._createTask(payload)}
                      ></home-upkeep-task-form>
                    </div>
                  </div>
                `
              : null}
            ${this._loading
              ? html`<div class="loading">
                  <div class="spinner"></div>
                  <span class="loading-text">Loading…</span>
                </div>`
              : null}
            ${this._error
              ? html`<div class="banner">
                  <svg
                    class="banner-icon"
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path
                      fill-rule="evenodd"
                      d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                      clip-rule="evenodd"
                    />
                  </svg>
                  <div class="banner-body">
                    <h3 class="banner-title">Error</h3>
                    <div class="banner-text">${this._error}</div>
                  </div>
                </div>`
              : null}
            ${this._selectedListId != null
              ? this._renderTaskSections()
              : null}
          </main>
        </div>
      </div>

      <home-upkeep-create-list-dialog
        .open=${this._creatingList}
        @dialog-close=${() => {
          this._creatingList = false;
        }}
        @dialog-save=${(e: CustomEvent<{ name: string }>) =>
          this._createList(e.detail.name)}
      ></home-upkeep-create-list-dialog>

      <home-upkeep-edit-task-dialog
        .task=${this._editingTask}
        @dialog-close=${() => {
          this._editingTask = null;
        }}
        @dialog-save=${(e: CustomEvent<{ payload: TaskUpdate }>) =>
          this._saveEditedTask(e.detail.payload)}
      ></home-upkeep-edit-task-dialog>

      <home-upkeep-edit-list-dialog
        .list=${this._editingList}
        @dialog-close=${() => {
          this._editingList = null;
        }}
        @dialog-save=${(e: CustomEvent<{ name: string }>) =>
          this._saveEditedList(e.detail.name)}
      ></home-upkeep-edit-list-dialog>

      <home-upkeep-snooze-dialog
        .open=${this._snoozingTask !== null}
        .taskTitle=${this._snoozingTask?.title ?? ""}
        @dialog-close=${() => {
          this._snoozingTask = null;
        }}
        @dialog-save=${(e: CustomEvent<{ period: string }>) =>
          this._saveSnooze(e.detail.period)}
      ></home-upkeep-snooze-dialog>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "home-upkeep-panel": HomeUpkeepPanel;
  }
}
