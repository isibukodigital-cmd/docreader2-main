"use strict";

(() => {
  const loginView = document.querySelector("#login-view");
  const onboardingView = document.querySelector("#onboarding-view");
  const chatView = document.querySelector("#chat-view");
  const loginForm = document.querySelector("#login-form");
  const loginButton = document.querySelector("#login-submit");
  const cognitoLoginButton = document.querySelector("#cognito-login");
  const loginError = document.querySelector("#login-error");
  const providerButtons = document.querySelectorAll("[data-provider]");
  const brandLogo = document.querySelector("#brand-logo");
  const uploadToggle = document.querySelector("#upload-toggle");
  const uploadForm = document.querySelector("#upload-form");
  const uploadSubmit = document.querySelector("#upload-submit");
  const uploadMessage = document.querySelector("#upload-message");
  const fileInput = document.querySelector("#document-file");
  const fileDropzone = document.querySelector("#file-dropzone");
  const selectedFile = document.querySelector("#selected-file");
  const selectedFileName = document.querySelector("#selected-file-name");
  const selectedFileSize = document.querySelector("#selected-file-size");
  const clearSelectedFile = document.querySelector("#clear-selected-file");
  const uploadAclGroups = document.querySelector("#acl-groups");
  const uploadAclAll = document.querySelector("#acl-all");
  const uploadAclWarning = document.querySelector("#acl-public-warning");
  const aclChipList = document.querySelector("#acl-chip-list");
  const workspaceGroupName = document.querySelector("#workspace-group-name");
  const createWorkspaceGroupButton = document.querySelector("#create-workspace-group");
  const workspaceGroupMessage = document.querySelector("#workspace-group-message");
  const workspaceMembers = document.querySelector("#workspace-members");
  const libraryToggle = document.querySelector("#library-toggle");
  const documentLibrary = document.querySelector("#document-library");
  const libraryList = document.querySelector("#library-list");
  const libraryMessage = document.querySelector("#library-message");
  const refreshLibraryButton = document.querySelector("#refresh-library");
  const librarySearch = document.querySelector("#library-search");
  const libraryAccessFilter = document.querySelector("#library-access-filter");
  const libraryCount = document.querySelector("#library-count");
  const dashboardToggle = document.querySelector("#dashboard-toggle");
  const adminDashboard = document.querySelector("#admin-dashboard");
  const dashboardSummary = document.querySelector("#dashboard-summary");
  const dashboardEvents = document.querySelector("#dashboard-events");
  const dashboardMessage = document.querySelector("#dashboard-message");
  const refreshDashboardButton = document.querySelector("#refresh-dashboard");
  const workspaceExportButton = document.querySelector("#workspace-export");
  const workspaceOffboardingButton = document.querySelector("#workspace-offboarding-request");
  const offboardingMessage = document.querySelector("#offboarding-message");
  const platformAdminToggle = document.querySelector("#platform-admin-toggle");
  const platformAdminView = document.querySelector("#platform-admin-view");
  const platformAdminBack = document.querySelector("#platform-admin-back");
  const platformOrganizationMessage = document.querySelector("#platform-org-message");
  const platformOrganizationList = document.querySelector("#platform-org-list");
  const platformOrganizationRequests = document.querySelector("#platform-organization-requests");
  const platformOffboardingRequests = document.querySelector("#platform-offboarding-requests");
  const platformAdminForm = document.querySelector("#platform-admin-form");
  const platformAdminSubmit = document.querySelector("#platform-admin-submit");
  const platformAdminMessage = document.querySelector("#platform-admin-message");
  const platformAdminList = document.querySelector("#platform-admin-list");
  const platformAdminAudit = document.querySelector("#platform-admin-audit");
  const workspaceForm = document.querySelector("#workspace-form");
  const workspaceSubmit = document.querySelector("#workspace-submit");
  const organizationSetup = document.querySelector("#organization-setup");
  const organizationGroupConnect = document.querySelector("#organization-group-connect");
  const graphConnectButton = document.querySelector("#graph-connect");
  const graphJoinConnectButton = document.querySelector("#graph-join-connect");
  const graphSyncButton = document.querySelector("#graph-sync");
  const organizationRequestStatus = document.querySelector("#organization-request-status");
  const organizationGroupConnectStatus = document.querySelector("#organization-group-connect-status");
  const organizationRequestForm = document.querySelector("#organization-request-form");
  const organizationGraphSummary = document.querySelector("#organization-graph-summary");
  const organizationGroupMappings = document.querySelector("#organization-group-mappings");
  const organizationRequestSubmit = document.querySelector("#organization-request-submit");
  const onboardingError = document.querySelector("#onboarding-error");
  const usagePanel = document.querySelector("#usage-panel");
  const retryButton = document.querySelector("#retry-question");
  const clearConversationButton = document.querySelector("#clear-conversation");
  const exportConversationButton = document.querySelector("#export-conversation");
  const exportMessage = document.querySelector("#export-message");
  const chatForm = document.querySelector("#chat-form");
  const questionInput = document.querySelector("#question");
  const questionCount = document.querySelector("#question-count");
  const sendButton = document.querySelector("#send-button");
  const chatError = document.querySelector("#chat-error");
  const conversation = document.querySelector("#conversation");
  const suggestions = document.querySelector("#suggestions");
  const signOutButton = document.querySelector("#sign-out");
  const sessionLabel = document.querySelector("#session-label");

  let config = null;
  let session = null;
  let sending = false;
  let isAdmin = false;
  let isAuditor = false;
  let isPlatformAdmin = false;
  let assistantName = "Document assistant";
  let onboardingAccountType = null;
  let onboardingOrganizationName = null;
  let graphCatalog = null;
  let lastQuestion = "";
  let libraryDocuments = [];
  let workspaceDirectory = { groups: [], members: [] };
  const verifierStorageKey = "docqa.oauth.pkce-verifier";
  const graphPurposeStorageKey = "docqa.oauth.graph-purpose";
  const flowStorageKey = "docqa.oauth.flow";
  const stateStorageKey = "docqa.oauth.state";
  const nonceStorageKey = "docqa.oauth.nonce";
  const sessionSnapshotStorageKey = "docqa.oauth.session-snapshot";

  brandLogo.addEventListener("error", () => {
    brandLogo.hidden = true;
    document.querySelector("#brand-default-mark").hidden = false;
  });

  graphJoinConnectButton.addEventListener("click", async () => {
    showMessage(onboardingError, "");
    graphJoinConnectButton.disabled = true;
    try {
      await signInWithProvider("azure", "join");
    } catch (error) {
      showMessage(onboardingError, error.message || "Microsoft Graph could not be connected.");
      graphJoinConnectButton.disabled = false;
    }
  });

  function showMessage(element, message) {
    element.textContent = message;
    element.hidden = !message;
  }

  function updateOnboardingProgress(step) {
    const steps = [...document.querySelectorAll(".onboarding-progress li")];
    steps.forEach((item, index) => {
      item.classList.toggle("is-active", index === step);
      item.classList.toggle("is-complete", index < step);
      if (index === step) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
  }

  function closeWorkspacePanels(except) {
    const panels = [
      [uploadToggle, uploadForm],
      [libraryToggle, documentLibrary],
      [dashboardToggle, adminDashboard],
    ];
    for (const [button, panel] of panels) {
      if (panel !== except) {
        panel.hidden = true;
        button?.classList.remove("is-active");
        button?.setAttribute("aria-pressed", "false");
      }
    }
  }

  function oauthErrorMessage(error) {
    if (/error getting user email from external provider/i.test(error)) {
      return "Your identity provider did not return an email address. For Microsoft sign-in, add the optional email claim to the Entra app's ID token and confirm the account has an email address.";
    }
    return error;
  }

  function updateComposerState() {
    questionCount.textContent = `${questionInput.value.length} / ${questionInput.maxLength}`;
    questionCount.classList.toggle("is-near-limit", questionInput.value.length >= 1800);
    questionCount.setAttribute(
      "aria-label",
      `${questionInput.value.length} of ${questionInput.maxLength} characters`,
    );
    sendButton.disabled = sending || !questionInput.value.trim();
  }

  function parseGroups(value) {
    return value.split(",").map((group) => group.trim()).filter(Boolean);
  }

  function normalizedGroups(value) {
    return JSON.stringify(parseGroups(value));
  }

  function renderAclChips() {
    if (!aclChipList) return;
    aclChipList.replaceChildren();
    for (const group of parseGroups(uploadAclGroups.value)) {
      if (group.toLocaleLowerCase() === "all") continue;
      const chip = document.createElement("span");
      chip.className = "acl-chip";
      chip.textContent = group;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${group}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        const remaining = parseGroups(uploadAclGroups.value).filter(
          (item) => item !== group,
        );
        uploadAclGroups.value = remaining.join(", ");
        renderAclChips();
        uploadAclGroups.focus();
        updateComposerState();
      });
      chip.append(remove);
      aclChipList.append(chip);
    }
  }

  function renderAccessChips(container, groups) {
    container.querySelectorAll(".acl-chip").forEach((chip) => chip.remove());
    if (!groups.length || groups.includes("all")) return;
    for (const group of groups) {
      const chip = document.createElement("span");
      chip.className = "acl-chip";
      chip.textContent = group;
      container.append(chip);
    }
  }

  function formatFileSize(bytes) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function showSelectedFile(file) {
    selectedFile.hidden = !file;
    fileDropzone.hidden = Boolean(file);
    if (file) {
      selectedFileName.textContent = file.name;
      selectedFileSize.textContent = `${formatFileSize(file.size)} · ${file.type || "Document"}`;
      if (file.size > 20 * 1024 * 1024) {
        uploadMessage.classList.add("message-error");
        showMessage(uploadMessage, "This file is larger than the 20 MiB upload limit. Choose another file.");
      } else {
        uploadMessage.classList.remove("message-error");
        showMessage(uploadMessage, "");
      }
    }
  }

  function updateAclCard(card) {
    const input = card.querySelector("[data-acl-input]");
    const save = card.querySelector('[data-document-action="save-acl"]');
    const status = card.querySelector(".acl-status");
    const groups = parseGroups(input.value);
    const isPublic = groups.includes("all");
    const isDirty = normalizedGroups(input.value) !== card.dataset.originalGroups;
    const hasDuplicates = new Set(groups).size !== groups.length;

    save.disabled = !isDirty || !groups.length || hasDuplicates;
    status.textContent = isDirty ? "Unsaved changes" : "Saved";
    status.classList.toggle("is-unsaved", isDirty);
    card.querySelector(".access-badge").textContent =
      isPublic ? "Everyone in workspace" : "Restricted access";
    card.querySelector(".access-badge").classList.toggle("is-public", isPublic);
    renderAccessChips(card.querySelector(".library-access-summary"), groups);
    const warning = card.querySelector(".access-warning");
    showMessage(
      warning,
      isPublic ? "This document will be available to everyone in your workspace." : "",
    );
  }

  function setSession(nextSession) {
    session = nextSession;
    const signedIn = Boolean(session && session.accessToken);
    loginView.hidden = signedIn;
    onboardingView.hidden = true;
    platformAdminView.hidden = true;
    graphSyncButton.hidden = true;
    chatView.hidden = !signedIn;
    signOutButton.hidden = !signedIn;
    if (!signedIn) {
      isAdmin = false;
      isAuditor = false;
      isPlatformAdmin = false;
      platformAdminToggle.hidden = true;
      platformAdminView.hidden = true;
      uploadForm.hidden = true;
      documentLibrary.hidden = true;
      adminDashboard.hidden = true;
      usagePanel.hidden = true;
      exportConversationButton.hidden = true;
      workspaceExportButton.hidden = true;
      workspaceOffboardingButton.hidden = true;
    }
    uploadToggle.hidden = !signedIn || !isAdmin;
    libraryToggle.hidden = !signedIn || !isAdmin;
    dashboardToggle.hidden = !signedIn || (!isAdmin && !isAuditor);
    sessionLabel.hidden = !signedIn || !session.email;
    sessionLabel.textContent = session && session.email ? session.email : "";
    if (signedIn) questionInput.focus();
  }

  async function loadConfig() {
    const response = await fetch("/v1/auth/config", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Chat sign-in is not configured. Ask your workspace administrator.");
    config = await response.json();
    if (config.auth_provider === "cognito") {
      loginForm.closest(".password-option").hidden = true;
      document.querySelector(".login-divider").hidden = true;
      cognitoLoginButton.hidden = false;
    }
  }

  async function loadBranding() {
    const response = await fetch("/v1/branding", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Workspace branding could not be loaded.");
    const branding = await response.json();
    document.title = branding.name || "Doc Reader";
    assistantName = branding.name || "Document assistant";
    document.querySelector("#brand-name").textContent = branding.name || "Doc Reader";
    document.querySelector("#brand-tagline").textContent = branding.tagline || "Document assistant";
    document.querySelector("#login-heading").textContent =
      branding.login_heading || "Find the answer in your documents.";
    document.querySelector("#login-subtitle").textContent =
      branding.login_subtitle || "Ask a question in plain language. Get a clear answer with sources you can open and verify.";
    document.querySelector("#footer-brand").textContent = branding.tagline || "DOCUMENTS, CONNECTED";
    document.querySelector("#footer-copy").textContent =
      branding.footer_text || "Answers may be imperfect. Verify important information in the cited source.";
    document.querySelector("#brand-attribution").hidden = Boolean(branding.hide_attribution);
    document.documentElement.style.setProperty("--green", branding.primary_color || "#245c4b");
    document.documentElement.style.setProperty("--paper", branding.accent_color || "#f5f6f2");
    if (branding.logo_url) {
      brandLogo.src = branding.logo_url;
      brandLogo.hidden = false;
      document.querySelector("#brand-default-mark").hidden = true;
    }
    if (branding.support_url) {
      const link = document.querySelector("#support-link");
      link.href = branding.support_url;
      link.textContent = branding.support_url.startsWith("mailto:") ? "Email support" : "Contact support";
      link.hidden = false;
    }
  }

  async function authRequest(path, body) {
    const response = await fetch(`${config.supabase_url}/auth/v1/${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: config.supabase_anon_key,
      },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 429) throw new Error("Too many sign-in attempts. Please wait a moment.");
      throw new Error(data.msg || data.message || data.error_description ||
        "Sign-in failed. Please try again or contact your workspace administrator.");
    }
    return data;
  }

  async function oauthTokenRequest(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 429) throw new Error("Too many sign-in attempts. Please wait a moment.");
      throw new Error(data.error_description || data.error || "Sign-in failed. Please try again.");
    }
    return data;
  }

  async function signIn(email, password) {
    const data = await authRequest("token?grant_type=password", { email, password });
    if (!data.access_token || !data.refresh_token) {
      throw new Error("Sign-in response was incomplete. Please try again.");
    }
    setSession({
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
      expiresAt: Date.now() + Math.max(0, Number(data.expires_in || 3600) - 30) * 1000,
      email: data.user && data.user.email ? data.user.email : email,
    });
    showMessage(loginError, "");
  }

  function base64Url(bytes) {
    let binary = "";
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  async function createPkcePair() {
    if (!window.crypto || !window.crypto.subtle) {
      throw new Error("Secure sign-in requires a modern browser on HTTPS or localhost.");
    }
    const bytes = new Uint8Array(32);
    window.crypto.getRandomValues(bytes);
    const verifier = base64Url(bytes);
    const digest = await window.crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(verifier),
    );
    return { verifier, challenge: base64Url(new Uint8Array(digest)) };
  }

  function randomOAuthValue() {
    const bytes = new Uint8Array(32);
    window.crypto.getRandomValues(bytes);
    return base64Url(bytes);
  }

  async function startCognitoSignIn(provider = null) {
    const { verifier, challenge } = await createPkcePair();
    const state = randomOAuthValue();
    const nonce = randomOAuthValue();
    sessionStorage.setItem(verifierStorageKey, verifier);
    sessionStorage.setItem(stateStorageKey, state);
    sessionStorage.setItem(nonceStorageKey, nonce);
    sessionStorage.setItem(flowStorageKey, "cognito");
    sessionStorage.removeItem(graphPurposeStorageKey);

    const authorizeUrl = new URL("/oauth2/authorize", config.cognito_domain);
    authorizeUrl.searchParams.set("client_id", config.cognito_client_id);
    authorizeUrl.searchParams.set("response_type", "code");
    authorizeUrl.searchParams.set("scope", "openid email profile");
    authorizeUrl.searchParams.set("redirect_uri", config.cognito_callback_url);
    authorizeUrl.searchParams.set("code_challenge", challenge);
    authorizeUrl.searchParams.set("code_challenge_method", "S256");
    authorizeUrl.searchParams.set("state", state);
    authorizeUrl.searchParams.set("nonce", nonce);
    if (provider) {
      authorizeUrl.searchParams.set(
        "identity_provider",
        provider === "google" ? "Google" : "Microsoft",
      );
    }
    window.location.assign(authorizeUrl.toString());
  }

  async function startMicrosoftGraphSignIn(graphPurpose) {
    if (!session) throw new Error("Sign in before connecting Microsoft Graph.");
    if (!config.microsoft_graph_client_id) {
      throw new Error("Microsoft Graph sign-in is not configured for this deployment.");
    }
    const { verifier, challenge } = await createPkcePair();
    const state = randomOAuthValue();
    sessionStorage.setItem(verifierStorageKey, verifier);
    sessionStorage.setItem(stateStorageKey, state);
    sessionStorage.setItem(flowStorageKey, "graph");
    sessionStorage.setItem(graphPurposeStorageKey, graphPurpose);
    sessionStorage.setItem(sessionSnapshotStorageKey, JSON.stringify(session));

    const authorizeUrl = new URL(
      "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize",
    );
    authorizeUrl.searchParams.set("client_id", config.microsoft_graph_client_id);
    authorizeUrl.searchParams.set("response_type", "code");
    authorizeUrl.searchParams.set("response_mode", "query");
    authorizeUrl.searchParams.set("redirect_uri", config.cognito_callback_url);
    authorizeUrl.searchParams.set(
      "scope",
      "openid profile email User.Read Group.Read.All RoleManagement.Read.Directory",
    );
    authorizeUrl.searchParams.set("code_challenge", challenge);
    authorizeUrl.searchParams.set("code_challenge_method", "S256");
    authorizeUrl.searchParams.set("state", state);
    authorizeUrl.searchParams.set("prompt", graphPurpose === "setup" ? "consent" : "select_account");
    window.location.assign(authorizeUrl.toString());
  }

  async function signInWithProvider(provider, graphPurpose = null) {
    if (!config) throw new Error("Sign-in is not ready yet. Please try again.");
    if (graphPurpose && provider !== "azure") {
      throw new Error("Microsoft Graph can only be connected with a Microsoft work or school account.");
    }
    if (config.auth_provider === "cognito") {
      if (graphPurpose) return startMicrosoftGraphSignIn(graphPurpose);
      return startCognitoSignIn(provider);
    }
    const { verifier, challenge } = await createPkcePair();
    sessionStorage.setItem(verifierStorageKey, verifier);
    if (graphPurpose) {
      sessionStorage.setItem(graphPurposeStorageKey, graphPurpose);
    } else {
      sessionStorage.removeItem(graphPurposeStorageKey);
    }

    const authorizeUrl = new URL("/auth/v1/authorize", config.supabase_url);
    authorizeUrl.searchParams.set("provider", provider);
    authorizeUrl.searchParams.set("redirect_to", `${window.location.origin}${window.location.pathname}`);
    authorizeUrl.searchParams.set("code_challenge", challenge);
    authorizeUrl.searchParams.set("code_challenge_method", "s256");
    authorizeUrl.searchParams.set(
      "scopes",
      graphPurpose
        ? "openid email profile User.Read Group.Read.All RoleManagement.Read.Directory"
        : "openid email profile",
    );
    authorizeUrl.searchParams.set(
      "prompt",
      graphPurpose === "setup" ? "consent" : "select_account",
    );
    window.location.assign(authorizeUrl.toString());
  }

  async function finishOAuthCallback() {
    const callbackUrl = new URL(window.location.href);
    const code = callbackUrl.searchParams.get("code");
    const error = callbackUrl.searchParams.get("error_description") ||
      callbackUrl.searchParams.get("error");
    const flow = sessionStorage.getItem(flowStorageKey);
    const verifier = sessionStorage.getItem(verifierStorageKey);
    const graphPurpose = sessionStorage.getItem(graphPurposeStorageKey);
    const expectedState = sessionStorage.getItem(stateStorageKey);
    const nonce = sessionStorage.getItem(nonceStorageKey);
    const sessionSnapshot = sessionStorage.getItem(sessionSnapshotStorageKey);
    if (!code && !error && !flow) return;

    window.history.replaceState(null, "", callbackUrl.pathname);
    sessionStorage.removeItem(verifierStorageKey);
    sessionStorage.removeItem(graphPurposeStorageKey);
    sessionStorage.removeItem(flowStorageKey);
    sessionStorage.removeItem(stateStorageKey);
    sessionStorage.removeItem(nonceStorageKey);
    if (
      (flow === "cognito" || flow === "graph") &&
      (!expectedState || callbackUrl.searchParams.get("state") !== expectedState)
    ) {
      if (flow === "graph" && sessionSnapshot) {
        setSession(JSON.parse(sessionSnapshot));
      } else {
        setSession(null);
      }
      sessionStorage.removeItem(sessionSnapshotStorageKey);
      throw new Error("Sign-in could not be verified. Please try again.");
    }
    if (error) {
      if (flow === "graph" && sessionSnapshot) {
        setSession(JSON.parse(sessionSnapshot));
        sessionStorage.removeItem(sessionSnapshotStorageKey);
      } else {
        setSession(null);
      }
      showMessage(loginError, oauthErrorMessage(error));
      return;
    }
    if (!code || !verifier) {
      throw new Error("Sign-in could not be completed. Please choose an account and try again.");
    }

    if (flow === "cognito" || flow === "graph") {
      if (flow === "cognito") {
        const data = await oauthTokenRequest(
          new URL("/oauth2/token", config.cognito_domain),
          {
            grant_type: "authorization_code",
            client_id: config.cognito_client_id,
            code,
            redirect_uri: config.cognito_callback_url,
            code_verifier: verifier,
          },
        );
        if (!data.access_token || !data.id_token || !data.refresh_token) {
          throw new Error("Cognito sign-in response was incomplete. Please try again.");
        }
        const identityClaims = decodeJwtPayload(data.id_token);
        if (!nonce || identityClaims.nonce !== nonce) {
          throw new Error("Cognito identity token could not be verified. Please sign in again.");
        }
        setSession({
          accessToken: data.access_token,
          idToken: data.id_token,
          refreshToken: data.refresh_token,
          expiresAt: Date.now() + Math.max(0, Number(data.expires_in || 3600) - 30) * 1000,
          email: typeof identityClaims.email === "string" ? identityClaims.email : "",
          graphAccessToken: null,
        });
        await loadPrincipal();
        conversation.replaceChildren();
        suggestions.hidden = false;
        showMessage(loginError, "");
        return;
      }

      if (!sessionSnapshot) {
        throw new Error("Your app session could not be restored. Please sign in again.");
      }
      const restoredSession = JSON.parse(sessionSnapshot);
      sessionStorage.removeItem(sessionSnapshotStorageKey);
      if (!restoredSession.accessToken || !restoredSession.refreshToken) {
        throw new Error("Your app session could not be restored. Please sign in again.");
      }
      setSession(restoredSession);
      const data = await oauthTokenRequest(
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
        {
          grant_type: "authorization_code",
          client_id: config.microsoft_graph_client_id,
          code,
          redirect_uri: config.cognito_callback_url,
          code_verifier: verifier,
          scope: "openid profile email User.Read Group.Read.All RoleManagement.Read.Directory",
        },
      );
      if (!data.access_token) {
        throw new Error("Microsoft Graph did not return an access token.");
      }
      setSession({ ...restoredSession, graphAccessToken: data.access_token });
      await loadPrincipal();
      await finishGraphPurpose(graphPurpose);
      return;
    }

    const data = await authRequest("token?grant_type=pkce", {
      auth_code: code,
      code_verifier: verifier,
    });
    if (!data.access_token || !data.refresh_token) {
      throw new Error("Sign-in response was incomplete. Please try again.");
    }
    setSession({
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
      expiresAt: Date.now() + Math.max(0, Number(data.expires_in || 3600) - 30) * 1000,
      email: data.user && data.user.email ? data.user.email : "",
      graphAccessToken: graphPurpose && typeof data.provider_token === "string"
        ? data.provider_token
        : null,
    });
    await loadPrincipal();
    if (graphPurpose && !session.graphAccessToken) {
      const message =
        "Microsoft did not return a Graph access token. Confirm delegated Graph permissions and tenant-admin consent, then connect again.";
      showMessage(graphPurpose === "setup" ? onboardingError : dashboardMessage, message);
    }
    await finishGraphPurpose(graphPurpose);
    conversation.replaceChildren();
    suggestions.hidden = false;
    showMessage(loginError, "");
  }

  function decodeJwtPayload(token) {
    try {
      const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      const encoded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
      const binary = atob(encoded);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      return JSON.parse(new TextDecoder().decode(bytes));
    } catch {
      throw new Error("Cognito returned an invalid identity token.");
    }
  }

  async function finishGraphPurpose(graphPurpose) {
    if (graphPurpose === "setup") {
      if (session.graphAccessToken) {
        try {
          await loadGraphCatalog();
        } catch (error) {
          showMessage(onboardingError, error.message || "Microsoft Graph groups could not be loaded.");
        }
      }
    } else if (graphPurpose === "join") {
      showMessage(
        organizationGroupConnectStatus,
        session.graphAccessToken
          ? "Microsoft Graph is connected. Continue to verify group membership and join."
          : "Microsoft Graph did not return an access token. Confirm delegated permissions and tenant-admin consent.",
      );
    } else if (graphPurpose === "sync") {
      if (session.graphAccessToken) {
        try {
          const result = await apiRequest("/v1/organization/graph/sync", {
            method: "POST",
            headers: { "X-Microsoft-Graph-Token": session.graphAccessToken },
          });
          await refreshSession();
          await loadPrincipal();
          showMessage(dashboardMessage, `${result.groups.length} Microsoft group mapping(s) synchronized.`);
        } catch (error) {
          showMessage(dashboardMessage, error.message || "Microsoft group sync could not complete.");
        }
      }
    }
  }

  async function refreshSession() {
    if (!session || !session.refreshToken) throw new Error("Your session expired. Please sign in again.");
    try {
      if (config.auth_provider === "cognito") {
        const data = await oauthTokenRequest(
          new URL("/oauth2/token", config.cognito_domain),
          {
            grant_type: "refresh_token",
            client_id: config.cognito_client_id,
            refresh_token: session.refreshToken,
          },
        );
        if (!data.access_token) throw new Error("Session refresh failed.");
        setSession({
          accessToken: data.access_token,
          idToken: data.id_token || session.idToken,
          refreshToken: data.refresh_token || session.refreshToken,
          expiresAt: Date.now() + Math.max(0, Number(data.expires_in || 3600) - 30) * 1000,
          email: data.id_token
            ? decodeJwtPayload(data.id_token).email || session.email
            : session.email,
          graphAccessToken: session.graphAccessToken || null,
        });
      } else {
        const data = await authRequest("token?grant_type=refresh_token", {
          refresh_token: session.refreshToken,
        });
        if (!data.access_token || !data.refresh_token) throw new Error("Session refresh failed.");
        setSession({
          accessToken: data.access_token,
          refreshToken: data.refresh_token,
          expiresAt: Date.now() + Math.max(0, Number(data.expires_in || 3600) - 30) * 1000,
          email: data.user && data.user.email ? data.user.email : session.email,
          graphAccessToken: session.graphAccessToken || null,
        });
      }
    } catch (error) {
      setSession(null);
      throw error;
    }
  }

  async function apiRequest(path, options = {}) {
    if (!session) throw new Error("Please sign in again to continue.");
    if (Date.now() >= session.expiresAt) await refreshSession();

    const responseError = async (response, fallback) => {
      const data = await response.json().catch(() => ({}));
      return typeof data.detail === "string" && data.detail
        ? data.detail
        : fallback;
    };

    const send = () => fetch(path, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body && !(options.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        Authorization: `Bearer ${session.accessToken}`,
        ...(session.idToken ? { "X-DocQA-Identity-Token": session.idToken } : {}),
        ...(options.headers || {}),
      },
    });

    let response = await send();
    if (response.status === 401) {
      const initialError = await response.clone().json().catch(() => ({}));
      const initialDetail = typeof initialError.detail === "string" ? initialError.detail : "";
      if (initialDetail && !/token is invalid or expired/i.test(initialDetail)) {
        setSession(null);
        throw new Error(initialDetail);
      }
      await refreshSession();
      response = await send();
    }
    if (response.status === 401) {
      setSession(null);
      throw new Error(await responseError(
        response,
        "Your session has ended. Please sign in again.",
      ));
    }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const detail = typeof data.detail === "string"
        ? data.detail
        : data.detail
          ? JSON.stringify(data.detail)
          : "The request failed. Please try again.";
      throw new Error(detail);
    }
    return response.json();
  }

  async function loadGraphCatalog() {
    if (!session || !session.graphAccessToken) {
      throw new Error("Connect Microsoft Graph again to load organization groups.");
    }
    const catalog = await apiRequest("/v1/organization/graph/catalog", {
      headers: { "X-Microsoft-Graph-Token": session.graphAccessToken },
    });
    graphCatalog = catalog;
    organizationRequestForm.hidden = false;
    updateOnboardingProgress(2);
    organizationGraphSummary.textContent =
      `${catalog.organization_name} · ${catalog.verified_domains.join(", ") || "No verified domains returned"} · ` +
      `${catalog.groups.length} security groups available`;
    const legend = document.createElement("legend");
    legend.textContent = "Choose groups and workspace group names";
    organizationGroupMappings.replaceChildren(legend);
    for (const [index, group] of catalog.groups.entries()) {
      const row = document.createElement("div");
      row.className = "organization-group-row";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.id = `organization-group-${index}`;
      checkbox.dataset.groupId = group.id;
      const label = document.createElement("label");
      label.htmlFor = checkbox.id;
      label.textContent = group.display_name || "(unnamed security group)";
      const mapping = document.createElement("input");
      mapping.type = "text";
      mapping.maxLength = 100;
      mapping.value = group.display_name || "";
      mapping.placeholder = "Workspace group";
      mapping.setAttribute("aria-label", `Workspace group name for ${label.textContent}`);
      row.append(checkbox, label, mapping);
      organizationGroupMappings.append(row);
    }
    if (!catalog.groups.length) {
      const empty = document.createElement("p");
      empty.className = "library-empty";
      empty.textContent = "No security groups were returned for this tenant.";
      organizationGroupMappings.append(empty);
      organizationRequestSubmit.disabled = true;
    } else {
      organizationRequestSubmit.disabled = false;
    }
    const status = catalog.request_status;
    showMessage(
      organizationRequestStatus,
      status === "pending"
        ? "A request is already awaiting review. Submitting again updates its selected groups."
        : status === "rejected"
          ? "A previous request was rejected. You can update the group mapping and submit again."
          : "",
    );
  }

  async function loadPrincipal() {
    try {
      graphSyncButton.hidden = true;
      const options = await apiRequest("/v1/onboarding/options");
      isPlatformAdmin = Boolean(options.is_platform_admin);
      platformAdminToggle.hidden = !isPlatformAdmin;
      if (options.onboarding_required) {
        updateOnboardingProgress(options.organization_setup_available || options.organization_graph_required ? 1 : 0);
        onboardingAccountType = options.account_type;
        onboardingOrganizationName = options.organization_name || null;
        organizationSetup.hidden = !options.organization_setup_available;
        organizationGroupConnect.hidden = !options.organization_graph_required;
        organizationRequestForm.hidden = true;
        graphCatalog = null;
        if (options.organization_graph_required) {
          showMessage(
            organizationGroupConnectStatus,
            session.graphAccessToken
              ? "Microsoft Graph is connected. Continue to verify group membership and join."
              : "",
          );
        }
        if (options.organization_request_status === "pending") {
          showMessage(
            organizationRequestStatus,
            "Your organization request is awaiting platform review. You can still create a private personal workspace.",
          );
        } else if (options.organization_request_status === "rejected") {
          showMessage(
            organizationRequestStatus,
            "A previous organization request was not approved. Connect Graph to review or update it.",
          );
        } else {
          showMessage(organizationRequestStatus, "");
        }
        if (isPlatformAdmin) {
          loginView.hidden = true;
          onboardingView.hidden = true;
          chatView.hidden = true;
          platformAdminView.hidden = false;
          platformAdminBack.hidden = true;
          await loadPlatformOrganizations();
          return;
        }
        if (!onboardingAccountType) {
          loginView.hidden = true;
          chatView.hidden = true;
          platformAdminView.hidden = true;
          onboardingView.hidden = false;
          document.querySelector("#onboarding-description").textContent =
            "We could not verify the Microsoft account type. Ask your administrator to check the Entra sign-in configuration.";
          workspaceForm.hidden = true;
          showMessage(onboardingError, "This account cannot start a preview until its Microsoft identity is verified.");
          return;
        }
        loginView.hidden = true;
        chatView.hidden = true;
        platformAdminView.hidden = true;
        onboardingView.hidden = false;
        workspaceForm.hidden = false;
        const isOrganization = onboardingAccountType === "organization";
        document.querySelector("#onboarding-title").textContent = isOrganization
          ? `Join ${onboardingOrganizationName || "your organization"}`
          : "Start your personal preview";
        document.querySelector("#onboarding-description").textContent = isOrganization
          ? "Your organization has been approved for a shared workspace. Join to access documents and collaborate with your colleagues."
          : "Create a private personal workspace now, or connect Microsoft Graph to request shared access for your organization.";
        document.querySelector("#workspace-name-label").hidden = isOrganization;
        document.querySelector("#workspace-name").hidden = isOrganization;
        document.querySelector("#workspace-name").required = false;
        document.querySelector("#workspace-name").placeholder = "Personal workspace";
        document.querySelector("#onboarding-member-limit").textContent =
          isOrganization ? "Up to 15 users" : "Up to 10 users";
        document.querySelector(".trial-limits div:nth-child(1) span").textContent =
          isOrganization ? "Organization preview" : "Personal plan";
        workspaceSubmit.querySelector("span").textContent =
          isOrganization ? "Join organization" : "Create personal workspace";
        showMessage(onboardingError, "");
        return;
      }

      const principal = await apiRequest("/v1/me");
      isAdmin = Boolean(principal.is_admin);
      isAuditor = Boolean(principal.is_auditor);
      isPlatformAdmin = Boolean(principal.is_platform_admin);
      graphSyncButton.hidden = !principal.organization_graph_sync_available;
      platformAdminToggle.hidden = !isPlatformAdmin;
      platformAdminView.hidden = true;
      uploadToggle.hidden = !isAdmin;
      libraryToggle.hidden = !isAdmin;
      dashboardToggle.hidden = !(isAdmin || isAuditor);
      workspaceExportButton.hidden = !isAdmin;
      workspaceOffboardingButton.hidden = !isAdmin;
      await loadUsage();
      if (isAdmin) {
        await loadWorkspaceDirectory();
        await loadDocumentLibrary();
      }
    } catch (error) {
      if (error.message.startsWith("Microsoft group membership sync is missing or older than 12 hours.")) {
        isAdmin = false;
        isAuditor = false;
        isPlatformAdmin = false;
        loginView.hidden = true;
        onboardingView.hidden = true;
        platformAdminView.hidden = true;
        chatView.hidden = false;
        graphSyncButton.hidden = false;
        dashboardToggle.hidden = true;
        uploadToggle.hidden = true;
        libraryToggle.hidden = true;
        workspaceExportButton.hidden = true;
        workspaceOffboardingButton.hidden = true;
        showMessage(dashboardMessage, error.message);
        return;
      }
      setSession(null);
      throw error;
    }
  }

  async function loadPlatformOrganizations() {
    try {
      const organizations = await apiRequest("/v1/platform/organizations");
      platformOrganizationList.replaceChildren();
      for (const organization of organizations) {
        const row = document.createElement("article");
        row.className = "library-card";
        const title = document.createElement("strong");
        title.textContent = organization.name;
        const details = document.createElement("span");
        details.textContent =
          `${organization.microsoft_tenant_id} · Initial admins: ${organization.initial_admin_emails.join(", ")}`;
        const remove = document.createElement("button");
        remove.className = "button button-quiet button-small";
        remove.type = "button";
        remove.textContent = "Unregister";
        remove.addEventListener("click", async () => {
          const confirmed = window.confirm(
            "Unregister this tenant? New users will return to personal previews and need a new Graph request for shared access; existing members keep their current workspace access.",
          );
          if (!confirmed) return;
          try {
            await apiRequest(`/v1/platform/organizations/${encodeURIComponent(organization.microsoft_tenant_id)}`, {
              method: "DELETE",
            });
            await loadPlatformOrganizations();
            showMessage(platformOrganizationMessage, "Organization unregistered.");
          } catch (error) {
            showMessage(platformOrganizationMessage, error.message || "Organization could not be unregistered.");
          }
        });
        row.append(title, details, remove);
        platformOrganizationList.append(row);
      }
      if (!organizations.length) {
        const empty = document.createElement("p");
        empty.className = "library-empty";
        empty.textContent = "No organizations are registered yet.";
        platformOrganizationList.append(empty);
      }
    } catch (error) {
      showMessage(platformOrganizationMessage, error.message || "Organization registrations could not be loaded.");
    }
    await loadPlatformOrganizationRequests();
    await loadPlatformOffboardingRequests();
    await loadPlatformAccessControl();
  }

  async function loadPlatformOffboardingRequests() {
    try {
      const requests = await apiRequest("/v1/platform/offboarding-requests");
      platformOffboardingRequests.replaceChildren();
      for (const offboarding of requests) {
        const row = document.createElement("article");
        row.className = "library-card";
        const title = document.createElement("strong");
        title.textContent = `${offboarding.workspace_name} · ${offboarding.workspace_type} workspace`;
        const detail = document.createElement("span");
        detail.textContent =
          `${offboarding.requester_email || offboarding.requester_user_id} · ` +
          `Requested ${new Date(offboarding.requested_at).toLocaleString()}`;
        const approve = document.createElement("button");
        approve.className = "button button-primary button-small";
        approve.type = "button";
        approve.textContent = "Approve deletion";
        approve.addEventListener("click", async () => {
          if (!window.confirm(
            `Permanently delete "${offboarding.workspace_name}" documents, indexed content, membership and query audit events? Supabase Auth accounts will remain.`,
          )) return;
          approve.disabled = true;
          try {
            await apiRequest(
              `/v1/platform/offboarding-requests/${encodeURIComponent(offboarding.request_id)}/review`,
              {
                method: "POST",
                body: JSON.stringify({ approve: true, review_note: "" }),
              },
            );
            showMessage(platformOrganizationMessage, "Workspace offboarding approved and customer data deleted.");
            await loadPlatformOffboardingRequests();
          } catch (error) {
            showMessage(platformOrganizationMessage, error.message || "Workspace offboarding could not be approved.");
          } finally {
            approve.disabled = false;
          }
        });
        const reject = document.createElement("button");
        reject.className = "button button-quiet button-small";
        reject.type = "button";
        reject.textContent = "Reject";
        reject.addEventListener("click", async () => {
          if (!window.confirm(`Reject the deletion request for "${offboarding.workspace_name}"?`)) return;
          reject.disabled = true;
          try {
            await apiRequest(
              `/v1/platform/offboarding-requests/${encodeURIComponent(offboarding.request_id)}/review`,
              {
                method: "POST",
                body: JSON.stringify({ approve: false, review_note: "" }),
              },
            );
            showMessage(platformOrganizationMessage, "Workspace offboarding request rejected.");
            await loadPlatformOffboardingRequests();
          } catch (error) {
            showMessage(platformOrganizationMessage, error.message || "Workspace offboarding request could not be rejected.");
          } finally {
            reject.disabled = false;
          }
        });
        row.append(title, detail, approve, reject);
        platformOffboardingRequests.append(row);
      }
      if (!requests.length) {
        const empty = document.createElement("p");
        empty.className = "library-empty";
        empty.textContent = "No workspace offboarding requests are awaiting review.";
        platformOffboardingRequests.append(empty);
      }
    } catch (error) {
      platformOffboardingRequests.replaceChildren();
      const failure = document.createElement("p");
      failure.className = "message message-error";
      failure.textContent = error.message || "Workspace offboarding requests could not be loaded.";
      platformOffboardingRequests.append(failure);
    }
  }

  async function loadPlatformOrganizationRequests() {
    try {
      const requests = await apiRequest("/v1/platform/organization-requests");
      platformOrganizationRequests.replaceChildren();
      for (const organizationRequest of requests) {
        const row = document.createElement("article");
        row.className = "library-card";
        const title = document.createElement("strong");
        title.textContent = organizationRequest.name;
        const details = document.createElement("span");
        const groups = organizationRequest.selected_groups
          .map((group) => `${group.display_name} → ${group.workspace_group_name}`)
          .join(", ");
        details.textContent =
          `${organizationRequest.microsoft_tenant_id} · ${organizationRequest.requester_email} · ` +
          `${organizationRequest.verified_domains.join(", ") || "No verified domains"} · Groups: ${groups}`;
        const approve = document.createElement("button");
        approve.className = "button button-primary button-small";
        approve.type = "button";
        approve.textContent = "Approve";
        approve.addEventListener("click", async () => {
          approve.disabled = true;
          try {
            await apiRequest(
              `/v1/platform/organization-requests/${encodeURIComponent(organizationRequest.request_id)}/review`,
              {
                method: "POST",
                body: JSON.stringify({ approve: true, review_note: "" }),
              },
            );
            showMessage(platformOrganizationMessage, "Organization approved. The verified requester becomes its first workspace admin.");
            await loadPlatformOrganizations();
          } catch (error) {
            showMessage(platformOrganizationMessage, error.message || "Organization request could not be approved.");
          } finally {
            approve.disabled = false;
          }
        });
        const reject = document.createElement("button");
        reject.className = "button button-quiet button-small";
        reject.type = "button";
        reject.textContent = "Reject";
        reject.addEventListener("click", async () => {
          if (!window.confirm(`Reject the organization request from ${organizationRequest.requester_email}?`)) return;
          reject.disabled = true;
          try {
            await apiRequest(
              `/v1/platform/organization-requests/${encodeURIComponent(organizationRequest.request_id)}/review`,
              {
                method: "POST",
                body: JSON.stringify({ approve: false, review_note: "" }),
              },
            );
            showMessage(platformOrganizationMessage, "Organization request rejected.");
            await loadPlatformOrganizationRequests();
          } catch (error) {
            showMessage(platformOrganizationMessage, error.message || "Organization request could not be rejected.");
          } finally {
            reject.disabled = false;
          }
        });
        row.append(title, details, approve, reject);
        platformOrganizationRequests.append(row);
      }
      if (!requests.length) {
        const empty = document.createElement("p");
        empty.className = "library-empty";
        empty.textContent = "No organization requests are awaiting review.";
        platformOrganizationRequests.append(empty);
      }
    } catch (error) {
      platformOrganizationRequests.replaceChildren();
      const failure = document.createElement("p");
      failure.className = "message message-error";
      failure.textContent = error.message || "Organization requests could not be loaded.";
      platformOrganizationRequests.append(failure);
    }
  }

  async function loadPlatformAccessControl() {
    try {
      const [admins, events] = await Promise.all([
        apiRequest("/v1/platform/admins"),
        apiRequest("/v1/platform/admin-audit"),
      ]);
      platformAdminList.replaceChildren();
      for (const administrator of admins) {
        const row = document.createElement("article");
        row.className = "library-card";
        const identity = document.createElement("strong");
        identity.textContent = administrator.email || administrator.user_id;
        const detail = document.createElement("span");
        detail.textContent = `Granted ${new Date(administrator.created_at).toLocaleString()}`;
        const revoke = document.createElement("button");
        revoke.className = "button button-quiet button-small";
        revoke.type = "button";
        revoke.textContent = "Revoke";
        revoke.disabled = admins.length <= 1;
        revoke.addEventListener("click", async () => {
          if (!window.confirm(`Revoke platform administrator access for ${identity.textContent}?`)) return;
          try {
            await apiRequest(`/v1/platform/admins/${encodeURIComponent(administrator.user_id)}`, {
              method: "DELETE",
            });
            await loadPlatformAccessControl();
            showMessage(platformAdminMessage, "Platform administrator access revoked.");
          } catch (error) {
            showMessage(platformAdminMessage, error.message || "Platform administrator access could not be revoked.");
          }
        });
        row.append(identity, detail, revoke);
        platformAdminList.append(row);
      }
      if (!admins.length) {
        const empty = document.createElement("p");
        empty.className = "library-empty";
        empty.textContent = "No platform administrators are registered.";
        platformAdminList.append(empty);
      }

      platformAdminAudit.replaceChildren();
      for (const event of events) {
        const row = document.createElement("article");
        row.className = "library-card";
        const action = document.createElement("strong");
        action.textContent = `${event.action === "grant" ? "Granted" : "Revoked"} platform access`;
        const detail = document.createElement("span");
        detail.textContent =
          `${event.target_email || event.target_user_id} · by ${event.actor_email || event.actor_user_id} · ` +
          new Date(event.created_at).toLocaleString();
        row.append(action, detail);
        platformAdminAudit.append(row);
      }
      if (!events.length) {
        const empty = document.createElement("p");
        empty.className = "library-empty";
        empty.textContent = "No platform administrator changes recorded yet.";
        platformAdminAudit.append(empty);
      }
    } catch (error) {
      showMessage(platformAdminMessage, error.message || "Platform administrator data could not be loaded.");
    }
  }

  async function loadWorkspaceDirectory() {
    try {
      workspaceDirectory = await apiRequest("/v1/workspace/directory");
      const options = document.querySelector("#workspace-group-options");
      options.replaceChildren();
      for (const group of workspaceDirectory.groups || []) {
        const option = document.createElement("option");
        option.value = group;
        options.append(option);
      }
      workspaceMembers.replaceChildren();
      for (const member of workspaceDirectory.members || []) {
        const row = document.createElement("article");
        row.className = "library-card";
        const identity = document.createElement("strong");
        identity.textContent = member.email || "Email unavailable";
        const detail = document.createElement("span");
        const groups = member.groups.length ? member.groups.join(", ") : "No groups";
        const roles = member.roles.length ? ` · Roles: ${member.roles.join(", ")}` : "";
        detail.textContent = `${groups}${roles}`;
        row.append(identity, detail);
        workspaceMembers.append(row);
      }
      if (!workspaceMembers.childElementCount) {
        const empty = document.createElement("p");
        empty.className = "library-empty";
        empty.textContent = "No registered members yet.";
        workspaceMembers.append(empty);
      }
    } catch (error) {
      workspaceDirectory = { groups: [], members: [] };
      showMessage(libraryMessage, error.message || "Workspace directory could not be loaded.");
    }
  }

  async function loadUsage() {
    try {
      const usage = await apiRequest("/v1/usage");
      usagePanel.hidden = false;
      document.querySelector("#usage-workspace-name").textContent = usage.workspace_name;
      document.querySelector("#usage-plan-label").textContent =
        usage.workspace_type === "organization" ? "Organization preview" : "Personal preview";
      document.querySelector("#usage-summary").textContent =
        `${usage.document_count}/${usage.document_limit} documents · ` +
        (usage.member_count_tracked
          ? `${usage.member_count}/${usage.member_limit} users`
          : "User count not yet tracked");
      document.querySelector("#document-usage-label").textContent =
        `${usage.document_count} of ${usage.document_limit}`;
      document.querySelector("#document-usage-bar").style.width =
        `${Math.min(100, 100 * usage.document_count / usage.document_limit)}%`;
      const memberUsage = document.querySelector("#member-usage");
      memberUsage.hidden = !usage.member_count_tracked;
      if (usage.member_count_tracked) {
        document.querySelector("#member-usage-label").textContent =
          `${usage.member_count} of ${usage.member_limit}`;
        document.querySelector("#member-usage-bar").style.width =
          `${Math.min(100, 100 * usage.member_count / usage.member_limit)}%`;
      }
      document.querySelector("#usage-upgrade-message").textContent = usage.upgrade_message;
    } catch (error) {
      usagePanel.hidden = false;
      document.querySelector("#usage-upgrade-message").textContent =
        error.message || "Workspace usage could not be loaded.";
    }
  }

  async function loadAdminDashboard() {
    showMessage(dashboardMessage, "Loading organization activity…");
    refreshDashboardButton.disabled = true;
    dashboardSummary.replaceChildren();
    dashboardEvents.replaceChildren();
    for (let index = 0; index < 3; index += 1) {
      const skeleton = document.createElement("div");
      skeleton.className = "skeleton-row";
      skeleton.setAttribute("aria-hidden", "true");
      dashboardEvents.append(skeleton);
    }
    try {
      const dashboard = await apiRequest("/v1/admin/dashboard");
      const metrics = [
        ["Members", dashboard.member_count],
        ["Documents", dashboard.document_count],
        ["Queries", dashboard.query_count],
        ["Query errors", dashboard.error_count],
        ["Document activity", dashboard.document_activity_count],
      ];
      for (const [label, value] of metrics) {
        const card = document.createElement("span");
        card.className = "usage-meter";
        card.textContent = `${label}: ${value}`;
        dashboardSummary.append(card);
      }
      dashboardEvents.replaceChildren();
      if (!dashboard.recent_events.length) {
        const empty = document.createElement("p");
        empty.className = "library-empty";
        empty.textContent = "No audit events recorded.";
        dashboardEvents.append(empty);
      } else {
        for (const event of dashboard.recent_events) {
          const row = document.createElement("article");
          row.className = "library-card";
          const title = document.createElement("strong");
          title.textContent = `${event.action} · ${event.outcome || "ok"}`;
          const detail = document.createElement("span");
          const cited = event.documents_cited.length
            ? ` · Cited: ${event.documents_cited.join(", ")}`
            : "";
          detail.textContent =
            `${new Date(event.created_at).toLocaleString()}${cited}`;
          row.append(title, detail);
          dashboardEvents.append(row);
        }
      }
      showMessage(dashboardMessage, "");
    } catch (error) {
      dashboardEvents.replaceChildren();
      showMessage(dashboardMessage, error.message || "Unable to load organization activity.");
    } finally {
      refreshDashboardButton.disabled = false;
    }
  }

  function renderDocumentLibrary() {
    libraryList.replaceChildren();
    const searchTerm = librarySearch.value.trim().toLocaleLowerCase();
    const accessFilter = libraryAccessFilter.value;
    const documents = libraryDocuments.filter((documentInfo) => {
      const groups = documentInfo.acl_groups || [];
      const isWorkspaceWide = groups.includes("all");
      const matchesSearch = `${documentInfo.name} ${groups.join(" ")}`
        .toLocaleLowerCase().includes(searchTerm);
      const matchesAccess = accessFilter === "all"
        || (accessFilter === "workspace" && isWorkspaceWide)
        || (accessFilter === "restricted" && !isWorkspaceWide);
      return matchesSearch && matchesAccess;
    });
    const total = libraryDocuments.length;
    libraryCount.textContent = documents.length === total
      ? `${total} ${total === 1 ? "document" : "documents"}`
      : `${documents.length} of ${total} documents`;

    if (!documents.length) {
      const empty = document.createElement("p");
      empty.className = "library-empty";
      empty.textContent = total
        ? "No documents match these search and access filters."
        : "No documents have been uploaded to this workspace yet.";
      libraryList.append(empty);
      return;
    }

    documents.forEach((documentInfo) => {
      const card = document.createElement("article");
      card.className = "library-card";
      card.dataset.documentId = documentInfo.document_id;

      const top = document.createElement("div");
      top.className = "library-card-top";
      const identity = document.createElement("div");
      identity.className = "library-document-identity";
      const name = document.createElement("strong");
      name.className = "library-document-name";
      name.textContent = documentInfo.name;
      const metadata = document.createElement("span");
      const created = new Date(documentInfo.created_at);
      const createdLabel = Number.isNaN(created.getTime())
        ? "Date unavailable"
        : created.toLocaleDateString();
      metadata.textContent = `${documentInfo.chunk_count} chunks · Added ${createdLabel}`;
      identity.append(name, metadata);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "button button-danger button-small";
      remove.dataset.documentAction = "delete";
      remove.textContent = "Delete";
      top.append(identity, remove);

      const accessSummary = document.createElement("div");
      accessSummary.className = "library-access-summary";
      const accessBadge = document.createElement("span");
      accessBadge.className = "access-badge";
      accessSummary.append(accessBadge);
      const accessStatus = document.createElement("span");
      accessStatus.className = "acl-status";
      accessSummary.append(accessStatus);

      const aclLabel = document.createElement("label");
      aclLabel.textContent = "Allowed groups";
      const aclControls = document.createElement("div");
      aclControls.className = "acl-controls";
      const aclInput = document.createElement("input");
      aclInput.type = "text";
      aclInput.value = documentInfo.acl_groups.join(", ");
      aclInput.maxLength = 510;
      aclInput.setAttribute("list", "workspace-group-options");
      aclInput.dataset.aclInput = "true";
      aclInput.setAttribute("aria-label", `Allowed groups for ${documentInfo.name}`);
      aclInput.setAttribute("aria-describedby", `acl-help-${documentInfo.document_id} acl-warning-${documentInfo.document_id}`);
      card.dataset.originalGroups = normalizedGroups(aclInput.value);
      const save = document.createElement("button");
      save.type = "button";
      save.className = "button button-quiet button-small";
      save.dataset.documentAction = "save-acl";
      save.textContent = "Save access";
      save.disabled = true;
      aclControls.append(aclInput, save);
      const hint = document.createElement("small");
      hint.id = `acl-help-${documentInfo.document_id}`;
      hint.textContent = "Use exact workspace group names, separated by commas.";
      const warning = document.createElement("p");
      warning.id = `acl-warning-${documentInfo.document_id}`;
      warning.className = "access-warning";
      warning.setAttribute("role", "status");
      warning.hidden = true;
      card.append(top, accessSummary, aclLabel, aclControls, hint, warning);
      libraryList.append(card);
      updateAclCard(card);
    });
  }

  async function loadDocumentLibrary() {
    libraryMessage.classList.remove("message-error");
    showMessage(libraryMessage, "Loading documents…");
    refreshLibraryButton.disabled = true;
    libraryList.replaceChildren();
    for (let index = 0; index < 3; index += 1) {
      const skeleton = document.createElement("div");
      skeleton.className = "skeleton-row";
      skeleton.setAttribute("aria-hidden", "true");
      libraryList.append(skeleton);
    }
    try {
      const documents = await apiRequest("/v1/documents");
      libraryDocuments = documents;
      renderDocumentLibrary();
      showMessage(libraryMessage, "");
    } catch (error) {
      libraryMessage.classList.add("message-error");
      showMessage(libraryMessage, error.message || "Unable to load documents.");
      libraryList.replaceChildren();
    } finally {
      refreshLibraryButton.disabled = false;
    }
  }

  function appendTurn(role, text, citations = [], found = null) {
    const turn = document.createElement("article");
    turn.className = "turn";

    const avatar = document.createElement("div");
    avatar.className = `turn-avatar${role === "user" ? " user" : ""}`;
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = role === "user" ? "Y" : "C";

    const content = document.createElement("div");
    content.className = "turn-content";
    const label = document.createElement("div");
    label.className = "turn-label";
    label.textContent = role === "user" ? "You" : assistantName;
    const body = document.createElement("div");
    body.className = "turn-text";
    body.textContent = text;
    content.append(label, body);

    if (role === "assistant" && found !== null) {
      const status = document.createElement("span");
      status.className = `answer-status${found ? "" : " is-refusal"}`;
      status.textContent = found ? "Answer found" : "No supported answer";
      content.insertBefore(status, body);
    }

    if (role === "assistant" && citations.length) {
      const list = document.createElement("div");
      list.className = "source-list";
      citations.forEach((citation, index) => {
        const card = document.createElement("div");
        card.className = "source-card";
        const number = document.createElement("button");
        number.className = "source-number";
        number.type = "button";
        number.textContent = String(index + 1);
        number.setAttribute("aria-label", `Preview source ${index + 1}: ${citation.document || "Source"}`);
        const details = document.createElement("div");
        const name = document.createElement("div");
        name.className = "source-name";
        name.textContent = citation.document || "Source";
        const location = document.createElement("span");
        location.className = "source-location";
        location.textContent = citation.location || "";
        name.append(location);
        details.append(name);
        if (citation.quote) {
          const preview = document.createElement("div");
          preview.className = "source-preview source-quote";
          preview.id = `source-preview-${conversation.childElementCount}-${index + 1}`;
          preview.hidden = true;
          preview.textContent = `“${citation.quote}”`;
          number.setAttribute("aria-controls", preview.id);
          number.setAttribute("aria-expanded", "false");
          number.addEventListener("click", () => {
            preview.hidden = !preview.hidden;
            number.setAttribute("aria-expanded", String(!preview.hidden));
          });
          details.append(preview);
        }
        card.append(number, details);
        list.append(card);
      });
      content.append(list);
    }

    if (role === "user" && text) {
      const actions = document.createElement("div");
      actions.className = "turn-actions";
      const reuse = document.createElement("button");
      reuse.type = "button";
      reuse.className = "turn-action";
      reuse.dataset.turnAction = "reuse";
      reuse.textContent = "Reuse question";
      actions.append(reuse);
      content.append(actions);
    }

    if (role === "assistant" && text) {
      const actions = document.createElement("div");
      actions.className = "turn-actions";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "turn-action";
      copy.dataset.turnAction = "copy";
      copy.textContent = "Copy answer";
      const status = document.createElement("span");
      status.className = "turn-action-status";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      actions.append(copy, status);
      content.append(actions);
    }

    turn.append(avatar, content);
    conversation.append(turn);
    if (role === "assistant" && text) exportConversationButton.hidden = false;
    turn.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return turn;
  }

  function exportConversation() {
    const turns = [...conversation.querySelectorAll(".turn")].filter(
      (turn) => !turn.querySelector(".typing") && turn.querySelector(".turn-text").textContent.trim(),
    );
    if (!turns.length) return;
    const transcript = turns.map((turn) => {
      const label = turn.querySelector(".turn-label").textContent.trim();
      const answer = turn.querySelector(".turn-text").textContent.trim();
      const sources = [...turn.querySelectorAll(".source-card")].map((source) => {
        const name = source.querySelector(".source-name").textContent.trim();
        const quote = source.querySelector(".source-quote");
        return `  - ${name}${quote ? `\n    ${quote.textContent.trim()}` : ""}`;
      });
      return `${label}\n${answer}${sources.length ? `\n\nSources:\n${sources.join("\n")}` : ""}`;
    }).join("\n\n---\n\n");
    const file = new Blob(
      [`${document.title}\nExported ${new Date().toLocaleString()}\n\n${transcript}\n`],
      { type: "text/plain;charset=utf-8" },
    );
    const downloadUrl = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = `doc-reader-conversation-${new Date().toISOString().slice(0, 10)}.txt`;
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
    showMessage(exportMessage, "Conversation exported to a text file on this device.");
    window.setTimeout(() => showMessage(exportMessage, ""), 4000);
  }

  exportConversationButton.addEventListener("click", exportConversation);

  function appendTyping() {
    const turn = appendTurn("assistant", "");
    const text = turn.querySelector(".turn-text");
    const indicator = document.createElement("span");
    indicator.className = "typing";
    indicator.setAttribute("aria-label", "Finding an answer");
    for (let i = 0; i < 3; i += 1) indicator.append(document.createElement("span"));
    text.append(indicator);
    return turn;
  }

  conversation.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-turn-action]");
    if (!button) return;
    const turn = button.closest(".turn");
    if (button.dataset.turnAction === "reuse") {
      questionInput.value = turn.querySelector(".turn-text").textContent.trim();
      questionInput.style.height = "auto";
      questionInput.style.height = `${Math.min(questionInput.scrollHeight, 130)}px`;
      updateComposerState();
      questionInput.focus();
      questionInput.setSelectionRange(questionInput.value.length, questionInput.value.length);
      return;
    }
    if (button.dataset.turnAction !== "copy") return;
    const text = turn.querySelector(".turn-text").textContent.trim();
    const citations = [...turn.querySelectorAll(".source-card")].map((source) => {
      const name = source.querySelector(".source-name").textContent.trim();
      const quote = source.querySelector(".source-quote");
      return `- ${name}${quote ? `\n  ${quote.textContent.trim()}` : ""}`;
    });
    const copiedText = citations.length
      ? `${text}\n\nSources:\n${citations.join("\n")}`
      : text;
    const status = turn.querySelector(".turn-action-status");
    button.disabled = true;
    try {
      await navigator.clipboard.writeText(copiedText);
      button.textContent = "Copied";
      showMessage(status, "Answer and sources copied.");
      window.setTimeout(() => {
        button.textContent = "Copy answer";
        showMessage(status, "");
      }, 2000);
    } catch {
      showMessage(status, "Could not copy. Check clipboard permissions and try again.");
    } finally {
      button.disabled = false;
    }
  });

  async function ask(question, appendUser = true) {
    if (sending || !question.trim()) return;
    sending = true;
    chatForm.setAttribute("aria-busy", "true");
    lastQuestion = question.trim();
    sendButton.disabled = true;
    clearConversationButton.disabled = true;
    retryButton.hidden = true;
    showMessage(chatError, "");
    suggestions.hidden = true;
    if (appendUser) appendTurn("user", question.trim());
    const typing = appendTyping();

    try {
      const result = await apiRequest("/v1/query", {
        method: "POST",
        body: JSON.stringify({ question: question.trim() }),
      });
      typing.remove();
      appendTurn("assistant", result.answer, result.citations || [], result.found);
      lastQuestion = "";
    } catch (error) {
      typing.remove();
      showMessage(chatError, error.message || "Unable to reach the document service.");
      retryButton.hidden = !session;
      if (!session) {
        uploadToggle.hidden = true;
        libraryToggle.hidden = true;
        uploadForm.hidden = true;
        documentLibrary.hidden = true;
        loginView.hidden = false;
        chatView.hidden = true;
      }
    } finally {
      sending = false;
      chatForm.setAttribute("aria-busy", "false");
      updateComposerState();
      clearConversationButton.disabled = false;
      questionInput.focus();
    }
  }

  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!config) return;
    showMessage(loginError, "");
    loginButton.disabled = true;
    const email = document.querySelector("#email").value.trim();
    const passwordInput = document.querySelector("#password");
    const password = passwordInput.value;
    try {
      await signIn(email, password);
      await loadPrincipal();
      passwordInput.value = "";
      conversation.replaceChildren();
      suggestions.hidden = false;
      showMessage(chatError, "");
    } catch (error) {
      showMessage(loginError, error.message || "Sign-in failed. Please try again.");
    } finally {
      loginButton.disabled = false;
    }
  });

  cognitoLoginButton.addEventListener("click", async () => {
    showMessage(loginError, "");
    cognitoLoginButton.disabled = true;
    try {
      await startCognitoSignIn();
    } catch (error) {
      showMessage(loginError, error.message || "Sign-in failed. Please try again.");
      cognitoLoginButton.disabled = false;
    }
  });

  graphConnectButton.addEventListener("click", async () => {
    showMessage(onboardingError, "");
    graphConnectButton.disabled = true;
    try {
      await signInWithProvider("azure", "setup");
    } catch (error) {
      showMessage(onboardingError, error.message || "Microsoft Graph could not be connected.");
      graphConnectButton.disabled = false;
    }
  });

  graphSyncButton.addEventListener("click", async () => {
    graphSyncButton.disabled = true;
    graphSyncButton.textContent = "Connecting Microsoft Graph…";
    try {
      await signInWithProvider("azure", "sync");
    } catch (error) {
      showMessage(dashboardMessage, error.message || "Microsoft group sync could not start.");
      graphSyncButton.disabled = false;
      graphSyncButton.textContent = "Sync Microsoft groups";
    }
  });

  organizationRequestForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!session || !session.graphAccessToken || !graphCatalog) {
      showMessage(onboardingError, "Connect Microsoft Graph again before submitting your organization request.");
      return;
    }
    showMessage(onboardingError, "");
    organizationRequestSubmit.disabled = true;
    try {
      const groups = [...organizationGroupMappings.querySelectorAll(".organization-group-row")]
        .filter((row) => row.querySelector('input[type="checkbox"]').checked)
        .map((row) => ({
          group_id: row.querySelector('input[type="checkbox"]').dataset.groupId,
          workspace_group_name: row.querySelector('input[type="text"]').value.trim(),
        }));
      if (!groups.length) {
        throw new Error("Select at least one security group for organization access.");
      }
      const result = await apiRequest("/v1/organization/requests", {
        method: "POST",
        headers: { "X-Microsoft-Graph-Token": session.graphAccessToken },
        body: JSON.stringify({ groups }),
      });
      graphCatalog.request_status = result.status;
      showMessage(
        organizationRequestStatus,
        "Organization request submitted. Platform review is required before shared workspace access is enabled.",
      );
      organizationRequestForm.hidden = true;
    } catch (error) {
      showMessage(onboardingError, error.message || "Organization request could not be submitted.");
    } finally {
      organizationRequestSubmit.disabled = false;
    }
  });

  workspaceForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!onboardingAccountType) return;
    showMessage(onboardingError, "");
    workspaceSubmit.disabled = true;
    workspaceSubmit.querySelector("span").textContent =
      onboardingAccountType === "organization" ? "Joining organization…" : "Creating workspace…";
    try {
      await apiRequest("/v1/onboarding", {
        method: "POST",
        headers: (
          onboardingAccountType === "organization" && session.graphAccessToken
            ? { "X-Microsoft-Graph-Token": session.graphAccessToken }
            : {}
        ),
        body: JSON.stringify({
          account_type: onboardingAccountType,
          workspace_name: document.querySelector("#workspace-name").value.trim(),
        }),
      });
      await refreshSession();
      await loadPrincipal();
      conversation.replaceChildren();
      suggestions.hidden = false;
    } catch (error) {
      showMessage(onboardingError, error.message || "Workspace setup failed.");
    } finally {
      workspaceSubmit.disabled = false;
      workspaceSubmit.querySelector("span").textContent =
        onboardingAccountType === "organization"
          ? "Join organization"
          : "Create personal workspace";
    }
  });

  platformAdminToggle.addEventListener("click", async () => {
    chatView.hidden = true;
    platformAdminView.hidden = false;
    platformAdminBack.hidden = false;
    showMessage(platformOrganizationMessage, "");
    await loadPlatformOrganizations();
  });

  platformAdminBack.addEventListener("click", () => {
    platformAdminView.hidden = true;
    chatView.hidden = false;
  });

  platformAdminForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMessage(platformAdminMessage, "");
    platformAdminSubmit.disabled = true;
    const emailInput = document.querySelector("#platform-admin-email");
    try {
      await apiRequest("/v1/platform/admins", {
        method: "POST",
        body: JSON.stringify({ email: emailInput.value.trim() }),
      });
      emailInput.value = "";
      await loadPlatformAccessControl();
      showMessage(platformAdminMessage, "Platform administrator access granted and audited.");
    } catch (error) {
      showMessage(platformAdminMessage, error.message || "Platform administrator access could not be granted.");
    } finally {
      platformAdminSubmit.disabled = false;
    }
  });

  providerButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      showMessage(loginError, "");
      providerButtons.forEach((providerButton) => { providerButton.disabled = true; });
      try {
        await signInWithProvider(button.dataset.provider);
      } catch (error) {
        showMessage(loginError, error.message || "Sign-in failed. Please try again.");
        providerButtons.forEach((providerButton) => { providerButton.disabled = false; });
      }
    });
  });

  chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = questionInput.value;
    questionInput.value = "";
    questionInput.style.height = "auto";
    updateComposerState();
    ask(question);
  });

  questionInput.addEventListener("input", () => {
    questionInput.style.height = "auto";
    questionInput.style.height = `${Math.min(questionInput.scrollHeight, 130)}px`;
    updateComposerState();
  });

  questionInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (!event.shiftKey || event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      chatForm.requestSubmit();
    }
  });

  suggestions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-question]");
    if (button) ask(button.dataset.question);
  });

  uploadToggle.addEventListener("click", () => {
    const opening = uploadForm.hidden;
    closeWorkspacePanels(opening ? uploadForm : null);
    uploadForm.hidden = !opening;
    uploadToggle.classList.toggle("is-active", opening);
    uploadToggle.setAttribute("aria-pressed", String(opening));
    if (opening) document.querySelector("#document-file").focus();
  });

  libraryToggle.addEventListener("click", async () => {
    const opening = documentLibrary.hidden;
    closeWorkspacePanels(opening ? documentLibrary : null);
    documentLibrary.hidden = !opening;
    libraryToggle.classList.toggle("is-active", opening);
    libraryToggle.setAttribute("aria-pressed", String(opening));
    if (opening) await loadDocumentLibrary();
  });

  refreshLibraryButton.addEventListener("click", loadDocumentLibrary);
  dashboardToggle.addEventListener("click", async () => {
    const opening = adminDashboard.hidden;
    closeWorkspacePanels(opening ? adminDashboard : null);
    adminDashboard.hidden = !opening;
    dashboardToggle.classList.toggle("is-active", opening);
    dashboardToggle.setAttribute("aria-pressed", String(opening));
    if (opening) await loadAdminDashboard();
  });
  refreshDashboardButton.addEventListener("click", loadAdminDashboard);

  workspaceExportButton.addEventListener("click", async () => {
    workspaceExportButton.disabled = true;
    showMessage(offboardingMessage, "");
    try {
      const exportData = await apiRequest("/v1/workspace/export");
      const blob = new Blob([JSON.stringify(exportData, null, 2)], {
        type: "application/json",
      });
      const download = document.createElement("a");
      const url = URL.createObjectURL(blob);
      download.href = url;
      download.download = `workspace-export-${new Date().toISOString().slice(0, 10)}.json`;
      download.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      showMessage(offboardingMessage, "Workspace export downloaded. Original uploaded files are not retained and are not included.");
    } catch (error) {
      showMessage(offboardingMessage, error.message || "Workspace export could not be downloaded.");
    } finally {
      workspaceExportButton.disabled = false;
    }
  });

  workspaceOffboardingButton.addEventListener("click", async () => {
    if (!window.confirm(
      "Request platform-admin approval to permanently delete this workspace's documents, indexed content, membership and query audit events? Auth accounts remain. You can continue using the workspace until the request is approved.",
    )) return;
    workspaceOffboardingButton.disabled = true;
    showMessage(offboardingMessage, "");
    try {
      await apiRequest("/v1/workspace/offboarding", { method: "POST" });
      showMessage(offboardingMessage, "Deletion request submitted. Workspace data remains available until a platform administrator reviews it.");
    } catch (error) {
      showMessage(offboardingMessage, error.message || "Workspace offboarding request could not be submitted.");
      workspaceOffboardingButton.disabled = false;
    }
  });
  createWorkspaceGroupButton.addEventListener("click", async () => {
    const name = workspaceGroupName.value.trim();
    showMessage(workspaceGroupMessage, "");
    workspaceGroupMessage.classList.remove("message-error");
    createWorkspaceGroupButton.disabled = true;
    try {
      const created = await apiRequest("/v1/workspace/groups", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      workspaceGroupName.value = "";
      await loadWorkspaceDirectory();
      showMessage(workspaceGroupMessage, `Group "${created.name}" is ready to assign.`);
    } catch (error) {
      workspaceGroupMessage.classList.add("message-error");
      showMessage(workspaceGroupMessage, error.message || "Unable to create group.");
    } finally {
      createWorkspaceGroupButton.disabled = false;
    }
  });
  librarySearch.addEventListener("input", renderDocumentLibrary);
  libraryAccessFilter.addEventListener("change", renderDocumentLibrary);

  uploadAclAll.addEventListener("change", () => {
    uploadAclGroups.disabled = uploadAclAll.checked;
    uploadAclGroups.required = !uploadAclAll.checked;
    if (uploadAclAll.checked) aclChipList.replaceChildren();
    else renderAclChips();
    showMessage(
      uploadAclWarning,
      uploadAclAll.checked ? "This document will be available to everyone in your workspace." : "",
    );
    if (!uploadAclAll.checked) uploadAclGroups.focus();
  });

  uploadAclGroups.addEventListener("input", renderAclChips);

  fileInput.addEventListener("change", () => showSelectedFile(fileInput.files[0] || null));

  clearSelectedFile.addEventListener("click", () => {
    fileInput.value = "";
    showSelectedFile(null);
    showMessage(uploadMessage, "");
    fileInput.focus();
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    fileDropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      fileDropzone.classList.add("is-dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    fileDropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      fileDropzone.classList.remove("is-dragging");
    });
  });

  fileDropzone.addEventListener("drop", (event) => {
    const files = event.dataTransfer && event.dataTransfer.files;
    if (!files || !files.length) return;
    fileInput.files = files;
    showSelectedFile(fileInput.files[0] || null);
  });

  libraryList.addEventListener("input", (event) => {
    const card = event.target.closest("[data-document-id]");
    if (card && event.target.matches("[data-acl-input]")) updateAclCard(card);
  });

  libraryList.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-document-action]");
    if (!button || !isAdmin) return;
    const card = button.closest("[data-document-id]");
    if (!card) return;
    const documentId = card.dataset.documentId;
    const action = button.dataset.documentAction;
    button.disabled = true;
    libraryMessage.classList.remove("message-error");
    showMessage(libraryMessage, "");
    try {
      if (action === "save-acl") {
        const aclInput = card.querySelector("[data-acl-input]");
        const groups = parseGroups(aclInput.value);
        if (!groups.length) throw new Error("Enter at least one group name.");
        if (new Set(groups).size !== groups.length) {
          throw new Error("Remove duplicate group names before saving.");
        }
        const result = await apiRequest(`/v1/documents/${encodeURIComponent(documentId)}/acl`, {
          method: "PATCH",
          body: JSON.stringify({ acl_groups: groups }),
        });
        if (!result.updated) throw new Error("Document was not found in this workspace.");
        card.dataset.originalGroups = normalizedGroups(aclInput.value);
        updateAclCard(card);
        showMessage(libraryMessage, "Document access updated.");
      } else if (action === "delete") {
        const name = card.querySelector(".library-document-name").textContent;
        if (!window.confirm(`Permanently delete "${name}" and its indexed content?`)) return;
        const result = await apiRequest(`/v1/documents/${encodeURIComponent(documentId)}`, {
          method: "DELETE",
        });
        if (!result.deleted) throw new Error("Document was not found in this workspace.");
        showMessage(libraryMessage, "Document and indexed content deleted.");
        await loadDocumentLibrary();
        await loadUsage();
      }
    } catch (error) {
      libraryMessage.classList.add("message-error");
      showMessage(libraryMessage, error.message || "Document update failed.");
    } finally {
      if (action === "delete") button.disabled = false;
      else updateAclCard(card);
    }
  });

  uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const aclInput = document.querySelector("#acl-groups");
    const file = fileInput.files[0];
    if (!file) {
      showMessage(uploadMessage, "Choose a document to upload.");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      showMessage(uploadMessage, "The selected file exceeds the 20 MiB limit.");
      return;
    }
    const aclGroups = uploadAclAll.checked ? ["all"] : parseGroups(aclInput.value);
    if (!aclGroups.length) {
      showMessage(uploadMessage, "Enter at least one workspace group, or choose everyone in this workspace.");
      return;
    }
    if (new Set(aclGroups).size !== aclGroups.length) {
      showMessage(uploadMessage, "Remove duplicate group names before uploading.");
      return;
    }

    uploadSubmit.disabled = true;
    uploadSubmit.textContent = "Uploading and indexing…";
    showMessage(uploadMessage, "");
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("acl_groups", aclGroups.join(","));
      const result = await apiRequest("/v1/documents", { method: "POST", body });
      showMessage(uploadMessage, `${result.name} added successfully (${result.chunks} text chunks indexed).`);
      uploadForm.reset();
      showSelectedFile(null);
      uploadAclGroups.disabled = false;
      uploadAclGroups.required = true;
      renderAclChips();
      showMessage(uploadAclWarning, "");
      await loadDocumentLibrary();
      await loadUsage();
    } catch (error) {
      showMessage(uploadMessage, error.message || "The document could not be uploaded.");
    } finally {
      uploadSubmit.disabled = false;
      uploadSubmit.textContent = "Upload and index";
    }
  });

  retryButton.addEventListener("click", () => {
    if (lastQuestion) ask(lastQuestion, false);
  });

  clearConversationButton.addEventListener("click", () => {
    conversation.replaceChildren();
    exportConversationButton.hidden = true;
    showMessage(exportMessage, "");
    suggestions.hidden = false;
    lastQuestion = "";
    retryButton.hidden = true;
    showMessage(chatError, "");
    questionInput.focus();
  });

  signOutButton.addEventListener("click", async () => {
    let revokeFailed = false;
    let cognitoLogoutUrl = null;
    if (session) {
      if (config.auth_provider === "cognito") {
        cognitoLogoutUrl = new URL("/logout", config.cognito_domain);
        cognitoLogoutUrl.searchParams.set("client_id", config.cognito_client_id);
        cognitoLogoutUrl.searchParams.set("logout_uri", config.cognito_logout_url);
        try {
          const response = await fetch(new URL("/oauth2/revoke", config.cognito_domain), {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
              client_id: config.cognito_client_id,
              token: session.refreshToken,
            }),
          });
          if (!response.ok) revokeFailed = true;
        } catch {
          revokeFailed = true;
        }
      } else {
        try {
          const response = await fetch(`${config.supabase_url}/auth/v1/logout`, {
            method: "POST",
            headers: {
              apikey: config.supabase_anon_key,
              Authorization: `Bearer ${session.accessToken}`,
            },
          });
          if (!response.ok) revokeFailed = true;
        } catch {
          revokeFailed = true;
        }
      }
    }
    setSession(null);
    uploadToggle.hidden = true;
    libraryToggle.hidden = true;
    platformAdminToggle.hidden = true;
    platformAdminView.hidden = true;
    uploadForm.hidden = true;
    documentLibrary.hidden = true;
    usagePanel.hidden = true;
    sessionStorage.removeItem(verifierStorageKey);
    sessionStorage.removeItem(flowStorageKey);
    sessionStorage.removeItem(stateStorageKey);
    sessionStorage.removeItem(nonceStorageKey);
    sessionStorage.removeItem(graphPurposeStorageKey);
    sessionStorage.removeItem(sessionSnapshotStorageKey);
    conversation.replaceChildren();
    document.querySelector("#password").value = "";
    if (cognitoLogoutUrl && !revokeFailed) {
      window.location.assign(cognitoLogoutUrl.toString());
      return;
    }
    if (providerButtons[0]) providerButtons[0].focus();
    showMessage(loginError, revokeFailed
      ? "Signed out on this device, but the identity provider could not revoke the remote session."
      : "");
    showMessage(chatError, "");
  });

  Promise.all([loadConfig(), loadBranding()])
    .then(finishOAuthCallback)
    .catch((error) => showMessage(loginError, error.message || "Sign-in failed. Please try again."));
})();
