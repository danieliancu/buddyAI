"""What the watch says when the question could not be transcribed (the speech-to-text provider stalled or
failed): a short apology and a request to repeat it, in the user's language. Spoken and shown like a reply,
instead of the generic "try again later" error screen."""

from __future__ import annotations

NOT_UNDERSTOOD: dict[str, str] = {
    "af": "Jammer, ek het dit nie verstaan nie. Kan jy dit asseblief herhaal?",
    "ar": "عذرًا، لم أفهم ذلك. هل يمكنك إعادته من فضلك؟",
    "hy": "Ներողություն, չհասկացա։ Կարո՞ղ եք կրկնել։",
    "az": "Bağışlayın, başa düşmədim. Zəhmət olmasa, təkrar edə bilərsinizmi?",
    "be": "Прабачце, я не зразумеў. Можаце паўтарыць, калі ласка?",
    "bs": "Izvini, nisam razumio. Možeš li ponoviti, molim te?",
    "bg": "Съжалявам, не разбрах. Можеш ли да повториш, моля?",
    "ca": "Ho sento, no ho he entès. M'ho pots repetir, si us plau?",
    "zh": "抱歉，我没听清楚。请你再说一遍好吗？",
    "hr": "Oprosti, nisam razumio. Možeš li ponoviti, molim te?",
    "cs": "Promiň, nerozuměl jsem. Můžeš to prosím zopakovat?",
    "da": "Undskyld, det forstod jeg ikke. Vil du gentage det?",
    "nl": "Sorry, dat heb ik niet verstaan. Kun je het herhalen?",
    "en": "Sorry, I didn't catch that. Could you say it again?",
    "et": "Vabandust, ma ei saanud aru. Kas saaksid seda korrata?",
    "fi": "Anteeksi, en saanut selvää. Voisitko toistaa?",
    "fr": "Désolé, je n'ai pas compris. Peux-tu répéter, s'il te plaît ?",
    "gl": "Desculpa, non o entendín. Podes repetilo, por favor?",
    "de": "Entschuldigung, das habe ich nicht verstanden. Kannst du es bitte wiederholen?",
    "el": "Συγγνώμη, δεν το κατάλαβα. Μπορείς να το επαναλάβεις;",
    "he": "סליחה, לא הבנתי. אפשר לחזור על זה?",
    "hi": "माफ़ कीजिए, मैं समझ नहीं पाया। क्या आप इसे दोहरा सकते हैं?",
    "hu": "Elnézést, ezt nem értettem. Megismételnéd, kérlek?",
    "is": "Afsakaðu, ég skildi þetta ekki. Geturðu endurtekið það?",
    "id": "Maaf, saya tidak menangkapnya. Bisa diulangi?",
    "it": "Scusa, non ho capito. Puoi ripetere, per favore?",
    "ja": "すみません、聞き取れませんでした。もう一度言っていただけますか？",
    "kn": "ಕ್ಷಮಿಸಿ, ನನಗೆ ಅರ್ಥವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಹೇಳುತ್ತೀರಾ?",
    "kk": "Кешіріңіз, түсінбедім. Қайталап айта аласыз ба?",
    "ko": "죄송해요, 잘 못 알아들었어요. 다시 말씀해 주시겠어요?",
    "lv": "Atvaino, es nesapratu. Vai vari, lūdzu, atkārtot?",
    "lt": "Atsiprašau, nesupratau. Ar galėtum pakartoti?",
    "mk": "Извини, не разбрав. Можеш ли да повториш, те молам?",
    "ms": "Maaf, saya tidak dapat menangkapnya. Boleh ulang semula?",
    "mi": "Aroha mai, kāore au i rongo. Ka taea e koe te kī anō?",
    "mr": "माफ करा, मला समजले नाही. कृपया पुन्हा सांगाल का?",
    "ne": "माफ गर्नुहोस्, मैले बुझिनँ। कृपया फेरि भन्न सक्नुहुन्छ?",
    "no": "Beklager, det fikk jeg ikke med meg. Kan du si det igjen?",
    "fa": "ببخشید، متوجه نشدم. می‌توانید دوباره بگویید؟",
    "pl": "Przepraszam, nie zrozumiałem. Czy możesz powtórzyć?",
    "pt": "Desculpa, não percebi. Podes repetir, por favor?",
    "ro": "Îmi pare rău, n-am înțeles. Poți să repeți, te rog?",
    "ru": "Извините, я не расслышал. Можете повторить, пожалуйста?",
    "sr": "Izvini, nisam razumeo. Možeš li da ponoviš, molim te?",
    "sk": "Prepáč, nerozumel som. Môžeš to prosím zopakovať?",
    "sl": "Oprosti, nisem razumel. Lahko ponoviš, prosim?",
    "es": "Perdona, no te he entendido. ¿Puedes repetirlo, por favor?",
    "sw": "Samahani, sikuelewa. Unaweza kurudia tafadhali?",
    "sv": "Förlåt, det uppfattade jag inte. Kan du säga det igen?",
    "tl": "Paumanhin, hindi ko naintindihan. Puwede mo bang ulitin?",
    "ta": "மன்னிக்கவும், எனக்குப் புரியவில்லை. மீண்டும் சொல்ல முடியுமா?",
    "th": "ขอโทษนะ ฟังไม่ทัน ช่วยพูดอีกครั้งได้ไหม",
    "tr": "Kusura bakma, anlayamadım. Tekrar eder misin?",
    "uk": "Вибачте, я не розчув. Можете повторити, будь ласка?",
    "ur": "معاف کیجیے، میں سمجھ نہیں پایا۔ کیا آپ دوبارہ کہہ سکتے ہیں؟",
    "vi": "Xin lỗi, tôi nghe không rõ. Bạn có thể nói lại được không?",
    "cy": "Mae'n ddrwg gen i, wnes i ddim deall. Allwch chi ei ddweud eto?",
}


def not_understood(language: str | None) -> tuple[str, str]:
    """(language, text): the apology in that language, English when it has none."""
    if language and language in NOT_UNDERSTOOD:
        return language, NOT_UNDERSTOOD[language]
    return "en", NOT_UNDERSTOOD["en"]
