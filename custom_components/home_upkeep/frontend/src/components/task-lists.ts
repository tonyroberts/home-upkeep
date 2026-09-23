import { mdiPencil, mdiTrashCanOutline, mdiUpload } from "@mdi/js";
import { LitElement, css, html } from "lit";
import { customElement, property, query } from "lit/decorators.js";

import type { ImportDoc, TaskList } from "../ha-api";
import { icon } from "../icon";
import {
  buttonStyles,
  cardStyles,
  dialogStyles,
  iconButtonStyles,
  listItemStyles,
} from "../styles";

@customElement("home-upkeep-task-lists")
export class HomeUpkeepTaskLists extends LitElement {
  @property({ attribute: false }) lists: TaskList[] = [];

  @property({ type: Number }) selectedListId: number | undefined;

  @property({ type: Boolean }) mobileMenuOpen = false;

  @query("#import-input") private _importInput?: HTMLInputElement;

  static styles = [
    cardStyles,
    buttonStyles,
    iconButtonStyles,
    listItemStyles,
    dialogStyles,
    css`
      aside {
        display: none;
      }
      aside.mobile-open {
        display: block;
        position: fixed;
        inset-block: 0;
        left: 0;
        z-index: 50;
        width: 20rem;
      }
      @media (min-width: 1024px) {
        aside {
          display: block;
        }
        aside.mobile-open {
          position: static;
          width: auto;
        }
      }
      .mobile-overlay {
        display: none;
      }
      .mobile-overlay.open {
        display: block;
        position: fixed;
        inset: 0;
        z-index: 40;
        background: rgb(0 0 0 / 0.5);
      }
      @media (min-width: 1024px) {
        .mobile-overlay.open {
          display: none;
        }
      }
      .card-inner {
        height: 100%;
        overflow-y: auto;
        padding: 1.5rem;
        box-sizing: border-box;
      }
      .header {
        margin-bottom: 1.5rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.5rem;
      }
      .header-actions {
        display: flex;
        gap: 0.5rem;
      }
      ul {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
      }
      .row {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .name-button {
        flex: 1;
        text-align: left;
        font-weight: 500;
        color: var(--hu-text);
        background: none;
        border: none;
        cursor: pointer;
        padding: 0;
        font-size: inherit;
        font-family: inherit;
      }
      .name-button:hover {
        color: var(--hu-primary);
      }
      .row-actions {
        margin-left: 0.5rem;
        display: flex;
        gap: 0.25rem;
      }
      .empty {
        padding: 1rem 0;
        text-align: center;
        font-size: 0.875rem;
        color: var(--hu-text-muted);
      }
    `,
  ];

  private _fire(name: string, detail?: unknown) {
    this.dispatchEvent(
      new CustomEvent(name, { detail, bubbles: true, composed: true }),
    );
  }

  private _selectList(id: number) {
    this._fire("list-select", { id });
    if (this.mobileMenuOpen) {
      this._fire("mobile-menu-toggle");
    }
  }

  private _createList() {
    this._fire("list-create");
    if (this.mobileMenuOpen) {
      this._fire("mobile-menu-toggle");
    }
  }

  private _openImport() {
    this._importInput?.click();
  }

  private async _handleImportFiles(e: Event): Promise<void> {
    const input = e.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = "";
    if (!files.length) return;

    const docs: ImportDoc[] = [];
    for (const file of files) {
      let json: unknown;
      try {
        json = JSON.parse(await file.text());
      } catch {
        alert(`${file.name} is not valid JSON.`);
        return;
      }
      if (
        typeof json !== "object" ||
        json === null ||
        !("list" in json) ||
        typeof (json as { list?: unknown }).list !== "object"
      ) {
        alert(`${file.name} doesn't look like a list_<id>.json export.`);
        return;
      }
      docs.push(json as ImportDoc);
    }

    this._fire("list-import", { docs });
    if (this.mobileMenuOpen) {
      this._fire("mobile-menu-toggle");
    }
  }

  render() {
    return html`
      ${this.mobileMenuOpen
        ? html`<div
            class="mobile-overlay open"
            @click=${() => this._fire("mobile-menu-toggle")}
          ></div>`
        : null}
      <aside class=${this.mobileMenuOpen ? "mobile-open" : ""}>
        <div class="card card-inner">
          <div class="header">
            <h2 class="dialog-title">Lists</h2>
            <div class="header-actions">
              <input
                id="import-input"
                type="file"
                accept="application/json,.json"
                multiple
                style="display: none;"
                @change=${(e: Event) => this._handleImportFiles(e)}
              />
              <button
                class="icon-button"
                aria-label="Import lists from add-on export"
                title="Import from add-on export"
                @click=${() => this._openImport()}
              >
                ${icon(mdiUpload)}
              </button>
              <button class="btn-primary" @click=${() => this._createList()}>
                New List
              </button>
            </div>
          </div>
          <nav>
            <ul>
              ${this.lists.map(
                (l) => html`
                  <li>
                    <div
                      class="list-item ${this.selectedListId === l.id
                        ? "list-item-selected"
                        : ""}"
                    >
                      <div class="row">
                        <button
                          class="name-button"
                          @click=${() => this._selectList(l.id)}
                        >
                          ${l.name}
                        </button>
                        <div class="row-actions">
                          <button
                            class="icon-button"
                            aria-label="Rename list ${l.name}"
                            title="Rename list"
                            @click=${() => this._fire("list-edit", { list: l })}
                          >
                            ${icon(mdiPencil)}
                          </button>
                          <button
                            class="icon-button-danger"
                            aria-label="Delete list ${l.name}"
                            title="Delete list"
                            @click=${() =>
                              this._fire("list-delete", { id: l.id })}
                          >
                            ${icon(mdiTrashCanOutline)}
                          </button>
                        </div>
                      </div>
                    </div>
                  </li>
                `,
              )}
              ${this.lists.length === 0
                ? html`<li class="empty">No lists yet</li>`
                : null}
            </ul>
          </nav>
        </div>
      </aside>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "home-upkeep-task-lists": HomeUpkeepTaskLists;
  }
}
