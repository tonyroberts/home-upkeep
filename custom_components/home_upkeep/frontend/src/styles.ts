import { css } from "lit";

/**
 * Semantic colour tokens, each aliasing a Home Assistant theme variable
 * with a fallback matching HA's default light theme.
 *
 * This is the panel's *only* coupling to HA's theme system: everything
 * else refers to `--hu-*` and never to an HA variable directly, so a
 * renamed or added HA variable is a one-line change here.
 *
 * There are deliberately no `@media (prefers-color-scheme: dark)` blocks
 * anywhere in the panel. That media query tracks the *operating system*
 * preference, which is not what HA's dark mode sets — HA resolves its own
 * theme (including its "auto" setting) and publishes the result through
 * these variables. Following them means the panel tracks whatever theme
 * the user has chosen, custom themes included, and needs no light/dark
 * branching of its own.
 *
 * Only the root panel (`entrypoint.ts`) includes this; CSS custom
 * properties inherit through shadow DOM boundaries, so every descendant
 * component's shadow root can reference `var(--hu-*)` without redefining
 * it — and a theme change reaches all of them at once.
 */
export const designTokens = css`
  :host {
    /* Surfaces */
    --hu-bg: var(--primary-background-color, #fafafa);
    --hu-surface: var(--card-background-color, #ffffff);
    --hu-surface-alt: var(--secondary-background-color, #e5e5e5);

    /* Text */
    --hu-text: var(--primary-text-color, #212121);
    --hu-text-muted: var(--secondary-text-color, #727272);
    --hu-text-on-accent: var(--text-primary-color, #ffffff);

    /* Lines */
    --hu-border: var(--divider-color, #e0e0e0);

    /* Accent */
    --hu-primary: var(--primary-color, #03a9f4);
    --hu-primary-strong: var(--dark-primary-color, #0288d1);

    /* Status */
    --hu-error: var(--error-color, #db4437);
    --hu-warning: var(--warning-color, #ffa600);
    --hu-success: var(--success-color, #43a047);
    --hu-info: var(--info-color, #039be5);

    /*
     * Translucent fills for badges, banners and selected rows. Mixing
     * toward transparent rather than a fixed pale shade lets the same
     * token sit correctly on a light or a dark surface, which is what the
     * old paired light/dark hex values were doing by hand.
     */
    --hu-primary-fill: color-mix(in srgb, var(--hu-primary) 14%, transparent);
    --hu-error-fill: color-mix(in srgb, var(--hu-error) 14%, transparent);
    --hu-error-line: color-mix(in srgb, var(--hu-error) 40%, transparent);
    --hu-warning-fill: color-mix(in srgb, var(--hu-warning) 18%, transparent);
    --hu-success-fill: color-mix(in srgb, var(--hu-success) 16%, transparent);
    --hu-info-fill: color-mix(in srgb, var(--hu-info) 16%, transparent);

    /*
     * Hover wash for controls that have no background of their own, so it
     * composites over whatever is behind them. Because it is mixed from
     * the text colour it darkens under a light theme and lightens under a
     * dark one.
     */
    --hu-hover: color-mix(in srgb, var(--hu-text) 8%, transparent);

    /* Elevation */
    --hu-shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
    --hu-shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1);
    --hu-shadow-lg: 0 20px 25px -5px rgb(0 0 0 / 0.15);

    font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
  }
`;

/** `.btn-primary` / `.btn-secondary` / `.btn-danger` */
export const buttonStyles = css`
  .btn-primary,
  .btn-secondary,
  .btn-danger {
    font-weight: 500;
    padding: 0.5rem 1rem;
    border-radius: 0.5rem;
    transition: background-color 0.2s;
    border: none;
    cursor: pointer;
    font-size: 0.875rem;
  }
  .btn-primary:disabled,
  .btn-secondary:disabled,
  .btn-danger:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
  .btn-primary {
    background: var(--hu-primary);
    color: var(--hu-text-on-accent);
  }
  .btn-primary:hover:not(:disabled) {
    background: var(--hu-primary-strong);
  }
  .btn-secondary {
    background: var(--hu-surface-alt);
    color: var(--hu-text);
  }
  .btn-secondary:hover:not(:disabled) {
    background: color-mix(in srgb, var(--hu-surface-alt) 90%, var(--hu-text));
  }
  .btn-danger {
    background: var(--hu-error);
    color: var(--hu-text-on-accent);
  }
  .btn-danger:hover:not(:disabled) {
    background: color-mix(in srgb, var(--hu-error) 88%, var(--hu-text));
  }
`;

/** `.input-field` */
export const inputStyles = css`
  .input-field {
    width: 100%;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--hu-border);
    border-radius: 0.5rem;
    background: var(--hu-surface);
    color: var(--hu-text);
    font-size: 0.875rem;
    font-family: inherit;
    box-sizing: border-box;
  }
  .input-field:focus {
    outline: none;
    box-shadow: 0 0 0 2px var(--hu-primary);
    border-color: transparent;
  }
`;

/** `.checkbox` */
export const checkboxStyles = css`
  .checkbox {
    height: 1rem;
    width: 1rem;
    accent-color: var(--hu-primary);
    border: 1px solid var(--hu-border);
    border-radius: 0.25rem;
  }
`;

/** `.icon-button` / `.icon-button-danger` */
export const iconButtonStyles = css`
  .icon-button,
  .icon-button-danger {
    background: none;
    border: none;
    cursor: pointer;
    padding: 0.25rem;
    border-radius: 0.25rem;
    display: inline-flex;
    color: var(--hu-text-muted);
  }
  .icon-button:hover {
    color: var(--hu-text);
  }
  .icon-button-danger:hover {
    color: var(--hu-error);
  }
`;

/** `.card` */
export const cardStyles = css`
  .card {
    background: var(--hu-surface);
    border-radius: 0.5rem;
    box-shadow: var(--hu-shadow-sm);
    border: 1px solid var(--hu-border);
  }
`;

/**
 * `.dialog` / `.dialog-body` / `.dialog-body-large` / `.dialog-content` /
 * `.dialog-title` / `.dialog-title-large` / `.dialog-label` /
 * `.dialog-label-inline` / `.dialog-help-text` / `.dialog-actions` /
 * `.dialog-actions-no-margin`
 */
export const dialogStyles = css`
  .dialog {
    position: fixed;
    inset: 0;
    background: rgb(0 0 0 / 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
    z-index: 50;
  }
  .dialog-body {
    background: var(--hu-surface);
    border-radius: 0.5rem;
    box-shadow: var(--hu-shadow-lg);
    width: 100%;
    max-width: 28rem;
  }
  .dialog-body-large {
    background: var(--hu-surface);
    border-radius: 0.5rem;
    box-shadow: var(--hu-shadow-lg);
    width: 100%;
    max-width: 42rem;
    max-height: 90vh;
    overflow-y: auto;
  }
  .dialog-content {
    padding: 1.5rem;
  }
  .dialog-title {
    font-size: 1.125rem;
    font-weight: 600;
    color: var(--hu-text);
    margin: 0 0 1rem;
  }
  .dialog-title-large {
    font-size: 1.125rem;
    font-weight: 600;
    color: var(--hu-text);
    margin: 0 0 1.5rem;
  }
  .dialog-label {
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--hu-text);
    margin-bottom: 0.25rem;
    display: block;
  }
  .dialog-label-inline {
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--hu-text);
  }
  .dialog-help-text {
    font-size: 0.75rem;
    color: var(--hu-text-muted);
    margin-top: 0.25rem;
  }
  .dialog-actions {
    display: flex;
    gap: 0.75rem;
    justify-content: flex-end;
    margin-top: 1.5rem;
  }
  .dialog-actions-no-margin {
    display: flex;
    gap: 0.75rem;
    justify-content: flex-end;
  }
`;

/**
 * `.badge-warning` / `.badge-info` / `.badge-orange` / `.badge-green`
 *
 * `.badge-warning` and `.badge-orange` now share the warning colour and
 * differ only in layout — HA's palette has one "needs attention" colour
 * where the old fixed palette had both a yellow and an orange.
 */
export const badgeStyles = css`
  .badge-warning,
  .badge-info {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.25rem 0.5rem;
    font-size: 0.75rem;
    font-weight: 500;
    border-radius: 0.25rem;
  }
  .badge-warning {
    background: var(--hu-warning-fill);
    color: var(--hu-text);
  }
  .badge-info {
    background: var(--hu-info-fill);
    color: var(--hu-text);
  }
  .badge-info button {
    margin-left: 0.25rem;
    background: none;
    border: none;
    cursor: pointer;
    color: var(--hu-info);
    font-size: inherit;
  }
  .badge-info button:hover {
    color: var(--hu-text);
  }
  .badge-orange,
  .badge-green {
    padding: 0.25rem 0.5rem;
    border-radius: 0.25rem;
  }
  .badge-orange {
    background: var(--hu-warning-fill);
    color: var(--hu-text);
  }
  .badge-green {
    background: var(--hu-success-fill);
    color: var(--hu-text);
  }
`;

/**
 * `.banner` / `.banner-dismissible` / `.banner-icon` / `.banner-body` /
 * `.banner-title` / `.banner-text` / `.banner-dismiss`
 *
 * Full-width alert strip in the theme's error colour. `.banner` is the
 * shell; add `.banner-dismissible` when a dismiss button sits opposite the
 * message. `.banner-title` is the bold leading line (on its own, or above
 * a `.banner-text` detail line); `.banner-icon` + `.banner-body` are for
 * the icon-then-text variant.
 *
 * The detail line uses the ordinary text colour rather than the error
 * colour, so it stays legible whatever `--error-color` a theme picks.
 */
export const bannerStyles = css`
  .banner {
    margin-bottom: 1.5rem;
    border-radius: 0.5rem;
    border: 1px solid var(--hu-error-line);
    background: var(--hu-error-fill);
    padding: 1rem;
    display: flex;
  }
  .banner-dismissible {
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }
  .banner-icon {
    height: 1.25rem;
    width: 1.25rem;
    color: var(--hu-error);
    flex-shrink: 0;
  }
  .banner-body {
    margin-left: 0.75rem;
  }
  .banner-title {
    margin: 0;
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--hu-error);
  }
  .banner-text {
    margin-top: 0.25rem;
    font-size: 0.875rem;
    color: var(--hu-text);
  }
  .banner-dismiss {
    border: none;
    background: none;
    color: var(--hu-error);
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 0.25rem;
    flex-shrink: 0;
  }
`;

/** `.loading` / `.spinner` / `.loading-text` */
export const spinnerStyles = css`
  .loading {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 3rem 0;
  }
  .spinner {
    height: 2rem;
    width: 2rem;
    border-radius: 9999px;
    border: 2px solid transparent;
    border-bottom-color: var(--hu-primary);
    animation: spin 1s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .loading-text {
    margin-left: 0.75rem;
    color: var(--hu-text-muted);
  }
`;

/**
 * `.task-list`
 *
 * The column a run of `<home-upkeep-task-item>`s sits in. Separate from
 * `taskItemStyles` because the two never apply in the same shadow root:
 * the container is the panel's, each item's own styling is its
 * component's.
 */
export const taskListStyles = css`
  .task-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
`;

/** `.task-item` */
export const taskItemStyles = css`
  .task-item {
    background: var(--hu-surface);
    border-radius: 0.5rem;
    border: 1px solid var(--hu-border);
    padding: 1rem;
    transition: box-shadow 0.2s;
  }
  .task-item:hover {
    box-shadow: var(--hu-shadow-md);
  }
`;

/** `.list-item` / `.list-item-selected` */
export const listItemStyles = css`
  .list-item {
    background: var(--hu-surface);
    border-radius: 0.5rem;
    border: 1px solid var(--hu-border);
    padding: 0.75rem;
    transition:
      box-shadow 0.2s,
      background-color 0.2s,
      border-color 0.2s;
  }
  .list-item:hover {
    box-shadow: var(--hu-shadow-sm);
  }
  .list-item-selected {
    background: var(--hu-primary-fill);
    border-color: var(--hu-primary);
    box-shadow: var(--hu-shadow-sm);
  }
`;

/**
 * `.section-title` / `.section-header` / `.count-due` / `.count-upcoming` /
 * `.count-completed` / `.empty-state` / `.empty-state-icon` /
 * `.empty-state-text`
 */
export const sectionStyles = css`
  .section-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: var(--hu-text);
    margin: 0;
  }
  .section-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
  }
  .count-due,
  .count-upcoming,
  .count-completed {
    font-size: 0.75rem;
    font-weight: 500;
    padding: 0.125rem 0.625rem;
    border-radius: 9999px;
    color: var(--hu-text);
  }
  .count-due {
    background: var(--hu-warning-fill);
  }
  .count-upcoming {
    background: var(--hu-info-fill);
  }
  .count-completed {
    background: var(--hu-success-fill);
  }
  .empty-state {
    text-align: center;
    padding: 2rem 0;
    color: var(--hu-text-muted);
  }
  .empty-state-icon {
    margin: 0 auto;
    height: 3rem;
    width: 3rem;
    color: var(--hu-text-muted);
  }
  .empty-state-text {
    margin-top: 0.5rem;
  }
`;

/** `.form-grid` / `.form-section` */
export const formStyles = css`
  .form-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 1rem;
  }
  @media (min-width: 768px) {
    .form-grid {
      grid-template-columns: 1fr 1fr;
    }
  }
  .form-section {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
`;

/**
 * `.app-toolbar` / `.app-toolbar-title` / `.hass-menu-button`
 *
 * Stands in for the Home Assistant frontend's own narrow-layout toolbar,
 * which core panels get from `hass-tabs-subpage`/`ha-menu-button` but a
 * bare `panel_custom` element does not — see `entrypoint.ts`'s
 * `_toggleHassMenu` for the full reasoning. The measurements here
 * (40px bar, 16px inline padding, 16px title) match the HA shell's, so
 * ours doesn't jump when navigating between panels.
 *
 * This is the one place that reads HA's `--app-header-*` variables
 * directly rather than going through a `--hu-*` token, because matching
 * the HA header is the whole point of the element — it should follow the
 * header's colours even in a theme that styles the header differently
 * from ordinary cards.
 *
 * `.hass-menu-button` therefore answers to Home Assistant's look, not
 * ours. It currently resembles `burgerStyles`' `.burger`, but only by
 * coincidence — see that export's note before unifying them.
 */
export const toolbarStyles = css`
  .app-toolbar {
    position: sticky;
    top: 0;
    z-index: 1;
    display: flex;
    align-items: center;
    color: var(--app-header-text-color, var(--hu-text));
    height: calc(40px + var(--safe-area-inset-top, 0px));
    padding: 0px 16px;
    background: var(--app-header-background-color, var(--hu-surface));
    border-bottom: 1px solid var(--hu-border);
    box-sizing: border-box;
  }
  .app-toolbar-title {
    margin-inline-start: 1.5rem;
    font-size: 16px;
    font-weight: 400;
    color: var(--app-header-text-color, var(--hu-text));
    line-height: 1.2;
    pointer-events: none;
  }
  .hass-menu-button {
    display: inline-flex;
    flex-shrink: 0;
    border: none;
    background: none;
    border-radius: 0.25rem;
    padding: 0.75rem;
    color: var(--app-header-text-color, var(--hu-text));
    cursor: pointer;
  }
  .hass-menu-button:hover {
    background: var(--hu-hover);
  }
`;

/**
 * `.burger`
 *
 * Toggles *this app's* own list sidebar on narrow layouts, where the
 * sidebar collapses off-screen; hidden from `lg` up, since the sidebar is
 * always visible there.
 *
 * Deliberately kept separate from `toolbarStyles`' `.hass-menu-button`
 * even though the two rule sets currently coincide. That one opens Home
 * Assistant's drawer and exists to look native to the HA shell, so it
 * follows HA's header colours; this one is part of our own UI and follows
 * the panel's. They are free to diverge, and merging them would couple two
 * things that only happen to look alike today.
 */
export const burgerStyles = css`
  .burger {
    display: inline-flex;
    border: none;
    background: none;
    border-radius: 0.25rem;
    padding: 0.5rem;
    color: var(--hu-text-muted);
    cursor: pointer;
  }
  .burger:hover {
    background: var(--hu-hover);
    color: var(--hu-text);
  }
  @media (min-width: 1024px) {
    .burger {
      display: none;
    }
  }
`;
