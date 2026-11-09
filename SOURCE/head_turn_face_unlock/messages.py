"""Text shown to people. Keys are stable; the service logs keys, people see the text."""

from __future__ import annotations

_TEXT = {
    "en": {
        "TURN_HEAD": "Turn your head slowly from right to left",
        "INFRARED_CANNOT_SEE": "The infrared camera cannot see your face: tilt the screen or move into view",
        "ACCEPTED": "Face recognized",
        "REJECTED": "Face not recognized",
        "CAMERA_BUSY": "The camera is in use by another program",
        "CAMERA_MISSING": "No color and infrared camera pair was found",
        "NOT_ENROLLED": "No face is enrolled for this user",
        "UNAVAILABLE": "Face unlock is not available",
        "NO_FACE": "no face seen",
        "RIGHT_TURN_MISSING": "the turn to the right was not seen",
        "LEFT_TURN_MISSING": "the turn to the left was not completed",
        "TURN_TOO_FAST": "the turn was too fast",
        "TURN_NOT_ONE_WAY": "the head did not turn in one direction",
        "TRACK_BROKEN": "the face left the picture or another face appeared during the turn",
        "COLOR_MISMATCH": "the face does not match",
        "INFRARED_TOO_FEW": "the infrared camera saw the face too rarely",
        "INFRARED_MISMATCH": "the face does not match in infrared",
        "INFRARED_NOT_LIVE": "the infrared reflection is too weak for a real face",
        "NOT_THREE_DIMENSIONAL": "the face moved like a flat picture, not like a head",
        "SHAPE_MISMATCH": "the proportions of the face do not match",
        "SHAPE_UNMEASURED": "the face was not seen from the front long enough",
        "TURN_SLOWER": "turn a little more slowly",
        "TIME_UP": "time is up",
        "CANCELLED": "cancelled",
        "ENROLL_LOOK_STRAIGHT": "Look straight at the camera.",
        "ENROLL_TURN_RIGHT": "Now turn your head slowly to YOUR RIGHT.",
        "ENROLL_SWEEP": "Now turn slowly from right to left and back until every angle reaches 100%.",
        "ENROLL_NOT_OWNER": "The face in view does not match the enrolled face; sample skipped.",
        "ENROLL_OWNER_MISMATCH": "This face does not match the existing enrollment; nothing was added.",
        "ENROLL_DIRECTION_MISMATCH": "The turn direction does not match the existing enrollment; nothing was added.",
        "ENROLL_TIME_UP": "Time is up; not every angle was collected.",
        "ENROLL_DONE": "Enrollment complete",
    },
    "tr": {
        "TURN_HEAD": "Başınızı yavaşça sağdan sola çevirin",
        "INFRARED_CANNOT_SEE": "Kızılötesi kamera yüzünüzü göremiyor: ekranı biraz yatırın ya da kadraja girin",
        "ACCEPTED": "Yüz tanındı",
        "REJECTED": "Yüz tanınmadı",
        "CAMERA_BUSY": "Kamera başka bir program tarafından kullanılıyor",
        "CAMERA_MISSING": "Renkli ve kızılötesi kamera çifti bulunamadı",
        "NOT_ENROLLED": "Bu kullanıcı için yüz kaydı yok",
        "UNAVAILABLE": "Yüz doğrulama şu an kullanılamıyor",
        "NO_FACE": "yüz görülmedi",
        "RIGHT_TURN_MISSING": "sağa dönüş görülmedi",
        "LEFT_TURN_MISSING": "sola dönüş tamamlanmadı",
        "TURN_TOO_FAST": "dönüş çok hızlıydı",
        "TURN_NOT_ONE_WAY": "baş tek yönde dönmedi",
        "TRACK_BROKEN": "dönüş sırasında yüz kadrajdan çıktı ya da ikinci bir yüz göründü",
        "COLOR_MISMATCH": "yüz eşleşmedi",
        "INFRARED_TOO_FEW": "kızılötesi kamera yüzü çok az gördü",
        "INFRARED_MISMATCH": "yüz kızılötesinde eşleşmedi",
        "INFRARED_NOT_LIVE": "kızılötesi yansıma gerçek bir yüz için çok zayıf",
        "NOT_THREE_DIMENSIONAL": "yüz bir baş gibi değil, düz bir resim gibi hareket etti",
        "SHAPE_MISMATCH": "yüzün oranları eşleşmedi",
        "SHAPE_UNMEASURED": "yüz yeterince uzun önden görülmedi",
        "TURN_SLOWER": "biraz daha yavaş dönün",
        "TIME_UP": "süre doldu",
        "CANCELLED": "iptal edildi",
        "ENROLL_LOOK_STRAIGHT": "Kameraya düz bakın.",
        "ENROLL_TURN_RIGHT": "Şimdi başınızı yavaşça SAĞINIZA çevirin.",
        "ENROLL_SWEEP": "Şimdi başınızı yavaşça sağdan sola, sonra soldan sağa çevirin; bütün açılar %100 olana kadar tekrarlayın.",
        "ENROLL_NOT_OWNER": "Kadrajdaki yüz kayıtlı yüze benzemiyor; örnek alınmadı.",
        "ENROLL_OWNER_MISMATCH": "Bu yüz mevcut kayıtla uyuşmuyor; ekleme yapılmadı.",
        "ENROLL_DIRECTION_MISMATCH": "Dönüş yönü mevcut kayıtla uyuşmuyor; ekleme yapılmadı.",
        "ENROLL_TIME_UP": "Süre doldu; bütün açılar toplanamadı.",
        "ENROLL_DONE": "Kayıt tamamlandı",
    },
}


def text(key: str, language: str = "en") -> str:
    table = _TEXT.get(language, _TEXT["en"])
    return table.get(key) or _TEXT["en"].get(key) or key


def keys() -> set[str]:
    return set(_TEXT["en"])
