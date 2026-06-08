function initIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
    }
}

function numberFromSelector(selector, fallback) {
    const element = document.querySelector(selector);
    const value = element ? Number(element.value) : Number.NaN;
    return Number.isFinite(value) ? value : fallback;
}

function formatCurrency(value) {
    return Number(value || 0).toLocaleString(undefined, {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
    });
}

function csrfHeaders(headers = {}) {
    const token = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
    return token ? { ...headers, "X-CSRFToken": token } : headers;
}

function readJsonPayload(id, fallback = null) {
    const node = document.getElementById(id);
    if (!node) return fallback;

    try {
        const value = JSON.parse(node.textContent || "null");
        return value === null ? fallback : value;
    } catch (error) {
        return fallback;
    }
}

function dashboardData() {
    return window.CREDISENSE || readJsonPayload("credisense-chart-data", {});
}

function latestPrediction() {
    return window.CREDISENSE_LAST_PREDICTION || readJsonPayload("credisense-last-prediction", null);
}

function addChatBubble(text, type) {
    const box = document.getElementById("chatMessages");
    if (!box) return;

    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${type}`;
    bubble.textContent = text;
    box.appendChild(bubble);
    box.scrollTop = box.scrollHeight;
}

const voiceState = {
    enabled: localStorage.getItem("credisenseVoiceEnabled") === "true",
    unlocked: false,
    speaking: false,
};

function voiceSupported() {
    return "speechSynthesis" in window;
}

function recognitionSupported() {
    return "SpeechRecognition" in window || "webkitSpeechRecognition" in window;
}

function setVoiceStatus(message) {
    const status = document.getElementById("voiceStatus");
    if (status) status.textContent = message;
}

function appContext() {
    const body = document.body || {};
    const dataset = body.dataset || {};
    return {
        page: dataset.page || "",
        role: dataset.role || "",
        roleLabel: dataset.roleLabel || "",
        userName: dataset.userName || "",
    };
}

function roleLabelFromRole(role) {
    const labels = {
        customer: "Customer",
        bank_officer: "Bank Officer",
        risk_admin: "Risk Admin",
    };
    return labels[role] || "User";
}

function roleFromEmail(email) {
    const normalized = String(email || "").toLowerCase();
    if (normalized.includes("admin@")) return "risk_admin";
    if (normalized.includes("officer@")) return "bank_officer";
    if (normalized.includes("customer@")) return "customer";
    return "";
}

function saveVoicePreference(enabled) {
    voiceState.enabled = enabled;
    localStorage.setItem("credisenseVoiceEnabled", String(enabled));
}

function setPendingRoleIntro(role) {
    if (role) {
        sessionStorage.setItem("credisensePendingRoleIntro", role);
    }
}

function updateVoiceToggle() {
    const toggle = document.getElementById("voiceToggle");
    if (!toggle) return;

    toggle.setAttribute("aria-pressed", String(voiceState.enabled));
    toggle.innerHTML = voiceState.enabled
        ? '<i data-lucide="volume-2"></i> Voice Ready'
        : '<i data-lucide="volume-x"></i> Enable Voice';
    initIcons();
}

function getPreferredVoice() {
    if (!voiceSupported()) return null;

    const voices = window.speechSynthesis.getVoices();
    const englishVoices = voices.filter((voice) => voice.lang && voice.lang.toLowerCase().startsWith("en"));
    return (
        englishVoices.find((voice) => /natural|jenny|aria|samantha|google|female/i.test(voice.name)) ||
        englishVoices[0] ||
        voices[0] ||
        null
    );
}

function normalizeSpeechText(text) {
    return String(text || "")
        .replace(/#/g, " application number ")
        .replace(/%/g, " percent")
        .replace(/\+/g, " plus ")
        .replace(/-/g, " minus ")
        .replace(/\$/g, " dollars ")
        .replace(/_/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}

function unlockVoiceFromGesture(options = {}) {
    if (!voiceSupported()) return false;
    if (voiceState.unlocked) return true;

    voiceState.unlocked = true;
    if (!options.silent) return true;

    try {
        const unlockUtterance = new SpeechSynthesisUtterance(".");
        unlockUtterance.volume = 0;
        unlockUtterance.rate = 1;
        window.speechSynthesis.speak(unlockUtterance);
        return true;
    } catch (error) {
        voiceState.unlocked = false;
        return false;
    }
}

function speakText(text, options = {}) {
    if (!voiceState.enabled && !options.force) return;
    if (!voiceSupported()) {
        setVoiceStatus("Voice output is not supported in this browser.");
        return;
    }
    if (!voiceState.unlocked && !options.userGesture && !options.autoAttempt) {
        setVoiceStatus(options.blockedStatus || "Click Enable Voice, Start Intro, or Speak Latest once to allow browser voice playback.");
        return;
    }
    if (options.userGesture) {
        unlockVoiceFromGesture();
    }

    const speechText = normalizeSpeechText(text);
    if (!speechText) return;

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(speechText);
    utterance.lang = "en-US";
    utterance.rate = options.rate || 0.92;
    utterance.pitch = options.pitch || 1;
    utterance.volume = 1;

    const voice = getPreferredVoice();
    if (voice) utterance.voice = voice;

    utterance.onstart = () => {
        voiceState.speaking = true;
        if (options.autoAttempt) voiceState.unlocked = true;
        setVoiceStatus(options.startStatus || "Speaking...");
    };
    utterance.onend = () => {
        voiceState.speaking = false;
        setVoiceStatus(options.endStatus || "Voice assistant ready.");
    };
    utterance.onerror = (event) => {
        voiceState.speaking = false;
        voiceState.unlocked = false;
        const reason = event.error ? ` (${event.error})` : "";
        setVoiceStatus(options.errorStatus || `Voice playback was blocked${reason}. Click Start Intro, Speak Latest, or Enable Voice again.`);
    };

    window.speechSynthesis.speak(utterance);
    return true;
}

function stopVoice() {
    if (voiceSupported()) window.speechSynthesis.cancel();
    voiceState.speaking = false;
    setVoiceStatus("Voice stopped.");
}

function prepareVoiceForUserAction() {
    if (voiceState.enabled) {
        unlockVoiceFromGesture({ silent: true });
    }
}

function buildLoginIntro() {
    return [
        "Welcome to CrediSense AI.",
        "This credit risk project turns a loan application into a clear lending decision with approval probability, default risk, policy intelligence, and model explanations.",
        "Customers can submit an application, hear the prediction, review the default probability meter, and track approved loan offers.",
        "Bank officers can search applicant records, inspect risk grades, and manage the review queue with approve, decline, or keep review actions.",
        "Risk admins can monitor portfolio exposure, top risk candidates, daily performers, and officer approval history.",
        "Choose a demo role, sign in, and the workspace will introduce the tools for that role.",
    ].join(" ");
}

function buildRoleIntro(context = appContext()) {
    const name = context.userName ? `, ${context.userName}` : "";
    const role = context.role || sessionStorage.getItem("credisensePendingRoleIntro") || "";

    if (role === "customer") {
        return [
            `Welcome back${name}. This is your CrediSense customer workspace.`,
            "Start with Score Application to enter applicant details, income, requested loan, term, credit score, and employment experience.",
            "After scoring, the Latest Score panel shows the decision, default probability meter, top model explanations, underwriting grade, pricing guidance, recommended safeguards, and a counterfactual approval rescue plan.",
            "Your approved offers appear in Take Loan, active balances appear in Loan Wallet, and the advisor can explain rejection reasons, safe loan size, policy grade, or improvement steps by voice.",
        ].join(" ");
    }

    if (role === "bank_officer") {
        return [
            `Welcome back${name}. This is your Bank Officer workspace in CrediSense AI.`,
            "Use Applicant Data to search by name, email, phone, or underwriting inputs and load previous applications with repayment context.",
            "The review queue highlights each application risk score, risk band, underwriting grade, and current status so you can approve, decline, or keep the case in review with an officer note.",
            "Analytics show portfolio split, risk trend, risk bands, and income versus loan exposure, while the advisor helps explain policy, pricing, and safe loan recommendations.",
        ].join(" ");
    }

    if (role === "risk_admin") {
        return [
            `Welcome back${name}. This is your Risk Admin command center.`,
            "Start with Portfolio Command to monitor total exposure, expected loss, open review exposure, approval rate, and risk band mix.",
            "Risk Insights surfaces top risk candidates, best daily candidates, and officer approval history so you can audit decisions and watch concentration risk.",
            "The review queue, analytics, system assurance checks, simulator, and voice advisor work together to help you govern credit policy and portfolio quality.",
        ].join(" ");
    }

    return [
        `Welcome back${name}. This is your CrediSense AI workspace.`,
        "Use the dashboard to score credit risk, review model explanations, monitor portfolio analytics, and work through the lending workflow with voice guidance.",
    ].join(" ");
}

function speakLoginIntro(options = {}) {
    saveVoicePreference(true);
    speakText(buildLoginIntro(), {
        force: true,
        userGesture: options.userGesture,
        autoAttempt: options.autoAttempt,
        startStatus: "Playing login intro...",
        endStatus: "Login voice intro complete.",
        errorStatus: "Voice playback was blocked. Click Play Intro once to start the login voice intro.",
        rate: 0.9,
    });
}

function speakRoleIntro(options = {}) {
    saveVoicePreference(true);
    updateVoiceToggle();
    speakText(buildRoleIntro(), {
        force: true,
        userGesture: options.userGesture,
        autoAttempt: options.autoAttempt,
        startStatus: "Playing workspace intro...",
        endStatus: "Workspace voice intro complete.",
        errorStatus: "Browser voice autoplay was blocked. Click Start Intro to hear the workspace intro.",
        rate: 0.9,
    });
}

function buildDecisionSpeech(prediction) {
    if (!prediction) return "";

    const rescuePlan = prediction.intelligence && prediction.intelligence.rescue_plan;
    const factors = (prediction.explain || [])
        .slice(0, 4)
        .map((factor) => {
            const direction = Number(factor.impact) >= 0 ? "supports approval" : "increases risk";
            return `${factor.feature} ${direction} with an impact of ${Math.abs(Number(factor.impact) || 0)} percent`;
        })
        .join(". ");

    const suggestions = (prediction.suggestions || []).slice(0, 3).join(". ");
    return [
        `CrediSense AI decision for application ${prediction.id}.`,
        `The result is ${prediction.result}.`,
        `Approval probability is ${prediction.approval_probability} percent.`,
        `Default risk is ${prediction.risk_score} percent, which is ${prediction.risk_label} risk.`,
        `Decision confidence is ${prediction.confidence} percent.`,
        prediction.intelligence
            ? `Underwriting grade is ${prediction.intelligence.grade}. Recommended action: ${prediction.intelligence.action}. Estimated monthly payment is ${prediction.intelligence.monthly_payment} dollars.`
            : "",
        rescuePlan
            ? `Approval rescue plan status is ${rescuePlan.status}. Target score is ${rescuePlan.target_score}, target loan is ${rescuePlan.target_loan} dollars, and readiness is ${rescuePlan.readiness_score} out of 100.`
            : "",
        prediction.auto_explain,
        factors ? `Key factors: ${factors}.` : "",
        suggestions ? `Recommendation: ${suggestions}.` : "",
    ]
        .filter(Boolean)
        .join(" ");
}

function speakLatestDecision(options = {}) {
    const prediction = latestPrediction();
    if (!prediction) {
        speakText("No latest prediction is available yet. Submit an application first.", options);
        return;
    }
    speakText(buildDecisionSpeech(prediction), options);
}

function initAuthDemoButtons() {
    document.querySelectorAll(".demo-login").forEach((button) => {
        button.addEventListener("click", () => {
            const email = document.getElementById("email");
            const password = document.getElementById("password");
            if (!email || !password) return;

            email.value = button.dataset.email || "";
            password.value = button.dataset.password || "";
            setPendingRoleIntro(button.dataset.role || roleFromEmail(email.value));
            setVoiceStatus(`${roleLabelFromRole(button.dataset.role)} demo selected. Sign in to hear the role intro.`);
            email.focus();
        });
    });
}

function initAuthVoiceIntro() {
    const context = appContext();
    if (context.page !== "login") return;

    if (!voiceSupported()) {
        setVoiceStatus("Voice output is not supported in this browser.");
        return;
    }

    const introButton = document.getElementById("authIntroVoice");
    if (introButton) {
        introButton.addEventListener("click", () => {
            speakLoginIntro({ userGesture: true });
        });
    }

    const stopButton = document.getElementById("authStopVoice");
    if (stopButton) {
        stopButton.addEventListener("click", stopVoice);
    }

    const form = document.querySelector(".auth-form");
    if (form) {
        form.addEventListener("submit", () => {
            const email = document.getElementById("email");
            setPendingRoleIntro(roleFromEmail(email ? email.value : ""));
        });
    }
}

function initCharts() {
    if (!window.Chart) return;

    const data = dashboardData();
    const chartFont = {
        family: "Inter",
        size: 12,
    };

    Chart.defaults.font = chartFont;
    Chart.defaults.color = "#64748b";
    Chart.defaults.plugins.legend.labels.usePointStyle = true;

    const decisionCanvas = document.getElementById("decisionChart");
    if (decisionCanvas) {
        new Chart(decisionCanvas, {
            type: "doughnut",
            data: {
                labels: ["Approved", "Rejected"],
                datasets: [
                    {
                        data: [data.approved || 0, data.rejected || 0],
                        backgroundColor: ["#15803d", "#dc2626"],
                        borderWidth: 0,
                    },
                ],
            },
            options: {
                cutout: "68%",
                plugins: {
                    legend: { position: "bottom" },
                },
            },
        });
    }

    const riskCanvas = document.getElementById("riskChart");
    if (riskCanvas) {
        new Chart(riskCanvas, {
            type: "line",
            data: {
                labels: data.labels || [],
                datasets: [
                    {
                        label: "Risk score",
                        data: data.risk_scores || [],
                        borderColor: "#b7791f",
                        backgroundColor: "rgba(183, 121, 31, 0.12)",
                        tension: 0.35,
                        fill: true,
                        pointRadius: 3,
                    },
                    {
                        label: "Credit score",
                        data: data.scores || [],
                        borderColor: "#4f46e5",
                        backgroundColor: "rgba(79, 70, 229, 0.08)",
                        tension: 0.35,
                        yAxisID: "score",
                    },
                ],
            },
            options: {
                scales: {
                    y: { min: 0, max: 100, grid: { color: "#eef2f7" } },
                    score: { position: "right", min: 300, max: 900, grid: { drawOnChartArea: false } },
                    x: { grid: { display: false } },
                },
            },
        });
    }

    const riskBandCanvas = document.getElementById("riskBandChart");
    if (riskBandCanvas) {
        const bands = data.risk_bands || {};
        new Chart(riskBandCanvas, {
            type: "bar",
            data: {
                labels: ["Low", "Medium", "High"],
                datasets: [
                    {
                        label: "Applications",
                        data: [bands.Low || 0, bands.Medium || 0, bands.High || 0],
                        backgroundColor: ["#15803d", "#b7791f", "#dc2626"],
                        borderRadius: 4,
                    },
                ],
            },
            options: {
                plugins: {
                    legend: { display: false },
                },
                scales: {
                    y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#eef2f7" } },
                    x: { grid: { display: false } },
                },
            },
        });
    }

    const exposureCanvas = document.getElementById("exposureChart");
    if (exposureCanvas) {
        new Chart(exposureCanvas, {
            type: "bar",
            data: {
                labels: data.labels || [],
                datasets: [
                    {
                        label: "Income",
                        data: data.income || [],
                        backgroundColor: "#0f9f8f",
                        borderRadius: 4,
                    },
                    {
                        label: "Loan",
                        data: data.loan || [],
                        backgroundColor: "#111827",
                        borderRadius: 4,
                    },
                ],
            },
            options: {
                responsive: true,
                scales: {
                    y: { grid: { color: "#eef2f7" } },
                    x: { grid: { display: false } },
                },
            },
        });
    }
}

function renderSimRescuePlan(plan) {
    const card = document.querySelector(".sim-rescue-card");
    if (!card || !plan) return;

    card.className = `sim-rescue-card ${plan.tone || "neutral"}`;

    const fields = {
        simRescueStatus: plan.status || "Path unavailable",
        simRescueSummary: plan.summary || "",
        simRescueReadiness: Number.isFinite(Number(plan.readiness_score)) ? `${plan.readiness_score}/100` : "--",
        simRescueTargetScore: plan.target_score || "--",
        simRescueTargetLoan: formatCurrency(plan.target_loan),
        simRescueRiskDelta: `${(-Number(plan.risk_reduction || 0)).toFixed(1)} pts`,
    };

    Object.entries(fields).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    });

    const steps = document.getElementById("simRescueSteps");
    if (!steps) return;

    steps.replaceChildren();
    (plan.steps || []).slice(0, 3).forEach((step) => {
        const item = document.createElement("div");
        item.className = `sim-rescue-step ${step.tone || "neutral"}`;

        const label = document.createElement("strong");
        label.textContent = step.label || "Next step";

        const detail = document.createElement("span");
        detail.textContent = `${step.current || "--"} -> ${step.target || "--"}`;

        item.append(label, detail);
        steps.appendChild(item);
    });
}

async function runSimulation() {
    const score = numberFromSelector("#simScore", 690);
    const income = numberFromSelector("#simIncome", 65000);
    const loan = numberFromSelector("#simLoan", 25000);
    const loanTerm = numberFromSelector("#simTerm", 36);
    const experience = numberFromSelector("#simExp", 5);

    const values = {
        simScoreValue: score,
        simIncomeValue: income,
        simLoanValue: loan,
        simTermValue: loanTerm,
        simExpValue: experience,
    };

    Object.entries(values).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value.toLocaleString();
    });

    const badge = document.getElementById("simBadge");
    if (badge) {
        badge.textContent = "Scoring";
        badge.className = "status-chip neutral";
    }

    try {
        const response = await fetch("/simulate", {
            method: "POST",
            headers: csrfHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
                score,
                income,
                loan,
                loan_term_months: loanTerm,
                experience,
            }),
        });
        const result = await response.json();

        const approval = document.getElementById("simApproval");
        const risk = document.getElementById("simRisk");
        if (approval) approval.textContent = `${result.approval_probability}%`;
        if (risk) risk.textContent = `${result.risk_score}%`;
        if (result.intelligence) {
            const intelligence = result.intelligence;
            const grade = document.getElementById("simGrade");
            const apr = document.getElementById("simApr");
            const payment = document.getElementById("simPayment");
            const limit = document.getElementById("simLimit");
            const action = document.getElementById("simAction");
            const policy = document.getElementById("simPolicy");

            if (grade) grade.textContent = intelligence.grade;
            if (apr) apr.textContent = `${intelligence.estimated_apr}%`;
            if (payment) payment.textContent = formatCurrency(intelligence.monthly_payment);
            if (limit) limit.textContent = formatCurrency(intelligence.recommended_limit);
            if (action) action.textContent = intelligence.action;
            if (policy) {
                const topFlag = (intelligence.policy_flags || [])[0];
                policy.textContent = topFlag ? `${topFlag.label}: ${topFlag.detail}` : "No policy flags.";
            }
            renderSimRescuePlan(intelligence.rescue_plan);
        }
        if (badge) {
            badge.textContent = result.result;
            badge.className = `status-chip ${result.result === "Approved" ? "approved" : "rejected"}`;
        }
    } catch (error) {
        if (badge) {
            badge.textContent = "Offline";
            badge.className = "status-chip rejected";
        }
    }
}

function initSimulator() {
    const controls = document.querySelectorAll("#simScore, #simIncome, #simLoan, #simTerm, #simExp");
    if (!controls.length) return;

    controls.forEach((control) => control.addEventListener("input", runSimulation));
    runSimulation();
}

async function sendChat(message) {
    const text = (message || document.getElementById("chatInput")?.value || "").trim();
    if (!text) return;

    addChatBubble(text, "user");
    const input = document.getElementById("chatInput");
    if (input) input.value = "";

    const payload = {
        message: text,
        score: numberFromSelector('[name="score"]', numberFromSelector("#simScore", 650)),
        income: numberFromSelector('[name="income"]', numberFromSelector("#simIncome", 50000)),
        loan: numberFromSelector('[name="loan"]', numberFromSelector("#simLoan", 20000)),
        loan_term_months: numberFromSelector('[name="loan_term_months"]', numberFromSelector("#simTerm", 36)),
        experience: numberFromSelector('[name="experience"]', numberFromSelector("#simExp", 2)),
    };

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: csrfHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        const reply = data.reply || "I could not generate a reply.";
        addChatBubble(reply, "bot");
        speakText(reply);
    } catch (error) {
        const reply = "The advisor is offline. Try again after the server restarts.";
        addChatBubble(reply, "bot");
        speakText(reply);
    }
}

function initChat() {
    const sendButton = document.getElementById("sendChat");
    const input = document.getElementById("chatInput");

    if (sendButton) {
        sendButton.addEventListener("click", () => {
            prepareVoiceForUserAction();
            sendChat();
        });
    }
    if (input) {
        input.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                prepareVoiceForUserAction();
                sendChat();
            }
        });
    }
    document.querySelectorAll("[data-question]").forEach((button) => {
        button.addEventListener("click", () => {
            prepareVoiceForUserAction();
            sendChat(button.dataset.question);
        });
    });
}

function initVoiceAssistant() {
    const context = appContext();
    if (context.page !== "dashboard") return;

    updateVoiceToggle();

    if (voiceSupported()) {
        window.speechSynthesis.onvoiceschanged = updateVoiceToggle;
    } else {
        setVoiceStatus("Voice output is not supported in this browser.");
    }

    if (!voiceState.enabled) {
        setVoiceStatus("Click Start Intro or Enable Voice to hear this workspace.");
    } else {
        setVoiceStatus("Voice ready. Click Start Intro for the workspace tour.");
    }

    const toggle = document.getElementById("voiceToggle");
    if (toggle) {
        toggle.addEventListener("click", () => {
            saveVoicePreference(!voiceState.enabled);
            updateVoiceToggle();
            if (voiceState.enabled) {
                speakRoleIntro({ userGesture: true });
            } else {
                stopVoice();
                setVoiceStatus("Voice assistant muted.");
            }
        });
    }

    const roleIntroButton = document.getElementById("roleIntroVoice");
    if (roleIntroButton) {
        roleIntroButton.addEventListener("click", () => {
            speakRoleIntro({ userGesture: true });
        });
    }

    const speakButton = document.getElementById("speakDecision");
    if (speakButton) {
        speakButton.addEventListener("click", () => {
            saveVoicePreference(true);
            updateVoiceToggle();
            speakLatestDecision({ force: true, userGesture: true });
        });
    }

    const stopButton = document.getElementById("stopVoice");
    if (stopButton) {
        stopButton.addEventListener("click", stopVoice);
    }

    const globalStopButton = document.getElementById("stopVoiceGlobal");
    if (globalStopButton) {
        globalStopButton.addEventListener("click", stopVoice);
    }

    const micButton = document.getElementById("voiceChat");
    if (micButton) {
        micButton.addEventListener("click", () => {
            unlockVoiceFromGesture({ silent: true });
            if (!recognitionSupported()) {
                speakText("Voice input is not supported in this browser.", { force: true });
                return;
            }

            const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            const recognition = new Recognition();
            recognition.lang = "en-US";
            recognition.interimResults = false;
            recognition.maxAlternatives = 1;

            recognition.onstart = () => {
                micButton.classList.add("voice-listening");
                setVoiceStatus("Listening. Ask about risk, rejection, safe loan size, or say explain prediction.");
            };

            recognition.onresult = (event) => {
                const transcript = event.results[0][0].transcript.trim();
                const input = document.getElementById("chatInput");
                if (input) input.value = transcript;

                if (/explain|prediction|decision|risk result/i.test(transcript)) {
                    addChatBubble(transcript, "user");
                    speakLatestDecision({ force: true, userGesture: true });
                } else {
                    sendChat(transcript);
                }
            };

            recognition.onerror = () => {
                setVoiceStatus("I could not hear that clearly. Try the mic again.");
            };

            recognition.onend = () => {
                micButton.classList.remove("voice-listening");
            };

            recognition.start();
        });
    }

    const pendingIntro = sessionStorage.getItem("credisensePendingRoleIntro");
    if (pendingIntro) {
        sessionStorage.removeItem("credisensePendingRoleIntro");
        if (voiceState.enabled) {
            speakRoleIntro({ autoAttempt: true });
        } else {
            setVoiceStatus("Workspace intro ready. Click Start Intro to hear the guided tour.");
        }
    }

    const prediction = latestPrediction();
    if (!pendingIntro && prediction && voiceState.enabled) {
        const predictionKey = `prediction-${prediction.id}-${prediction.result}-${prediction.risk_score}`;
        if (sessionStorage.getItem("credisenseLastSpokenPrediction") !== predictionKey) {
            sessionStorage.setItem("credisenseLastSpokenPrediction", predictionKey);
            setVoiceStatus("New decision ready. Click Speak Latest to hear the explanation.");
        }
    }
}

function initMemoActions() {
    const printButton = document.getElementById("printMemo");
    if (printButton) {
        printButton.addEventListener("click", () => window.print());
    }
}

document.addEventListener("DOMContentLoaded", () => {
    initIcons();
    initAuthDemoButtons();
    initAuthVoiceIntro();
    initCharts();
    initSimulator();
    initChat();
    initVoiceAssistant();
    initMemoActions();
});
