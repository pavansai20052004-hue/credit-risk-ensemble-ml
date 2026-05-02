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
    if (!voiceState.unlocked && !options.userGesture) {
        setVoiceStatus("Click Enable Voice or Speak Latest once to allow browser voice playback.");
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
    utterance.rate = 0.92;
    utterance.pitch = 1;
    utterance.volume = 1;

    const voice = getPreferredVoice();
    if (voice) utterance.voice = voice;

    utterance.onstart = () => {
        voiceState.speaking = true;
        setVoiceStatus("Speaking risk explanation...");
    };
    utterance.onend = () => {
        voiceState.speaking = false;
        setVoiceStatus("Voice assistant ready.");
    };
    utterance.onerror = (event) => {
        voiceState.speaking = false;
        voiceState.unlocked = false;
        const reason = event.error ? ` (${event.error})` : "";
        setVoiceStatus(`Voice playback was blocked${reason}. Click Speak Latest or Enable Voice again.`);
    };

    window.speechSynthesis.speak(utterance);
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

function buildDecisionSpeech(prediction) {
    if (!prediction) return "";

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
        prediction.auto_explain,
        factors ? `Key factors: ${factors}.` : "",
        suggestions ? `Recommendation: ${suggestions}.` : "",
    ]
        .filter(Boolean)
        .join(" ");
}

function speakLatestDecision(options = {}) {
    const prediction = window.CREDISENSE_LAST_PREDICTION;
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
            email.focus();
        });
    });
}

function initCharts() {
    if (!window.Chart || !window.CREDISENSE) return;

    const data = window.CREDISENSE;
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
            headers: { "Content-Type": "application/json" },
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
    };

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
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
    updateVoiceToggle();

    if (voiceSupported()) {
        window.speechSynthesis.onvoiceschanged = updateVoiceToggle;
    } else {
        setVoiceStatus("Voice output is not supported in this browser.");
    }

    if (!voiceState.enabled) {
        setVoiceStatus("Click Enable Voice before using spoken explanations.");
    }

    const toggle = document.getElementById("voiceToggle");
    if (toggle) {
        toggle.addEventListener("click", () => {
            voiceState.enabled = !voiceState.enabled;
            localStorage.setItem("credisenseVoiceEnabled", String(voiceState.enabled));
            updateVoiceToggle();
            if (voiceState.enabled) {
                speakText("Voice assistant enabled. I can explain risk predictions and advisor replies.", {
                    force: true,
                    userGesture: true,
                });
            } else {
                stopVoice();
                setVoiceStatus("Voice assistant muted.");
            }
        });
    }

    const speakButton = document.getElementById("speakDecision");
    if (speakButton) {
        speakButton.addEventListener("click", () => {
            voiceState.enabled = true;
            localStorage.setItem("credisenseVoiceEnabled", "true");
            updateVoiceToggle();
            speakLatestDecision({ force: true, userGesture: true });
        });
    }

    const stopButton = document.getElementById("stopVoice");
    if (stopButton) {
        stopButton.addEventListener("click", stopVoice);
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

    const prediction = window.CREDISENSE_LAST_PREDICTION;
    if (prediction && voiceState.enabled) {
        const predictionKey = `prediction-${prediction.id}-${prediction.result}-${prediction.risk_score}`;
        if (sessionStorage.getItem("credisenseLastSpokenPrediction") !== predictionKey) {
            sessionStorage.setItem("credisenseLastSpokenPrediction", predictionKey);
            setVoiceStatus("New decision ready. Click Speak Latest to hear the explanation.");
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    initIcons();
    initAuthDemoButtons();
    initCharts();
    initSimulator();
    initChat();
    initVoiceAssistant();
});
