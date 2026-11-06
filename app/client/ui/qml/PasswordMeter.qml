import QtQuick
import QtQuick.Layouts

// Four bars and a word under a new password. Only length and a few different characters are required;
// the bars show how hard the password would be to guess, so a long phrase of plain words scores well.
ColumnLayout {
    id: meter
    property string value: ""
    readonly property var verdict: rate(value)
    visible: value.length > 0
    spacing: 5

    RowLayout {
        Layout.fillWidth: true; spacing: 5
        Repeater {
            model: 4
            delegate: Rectangle {
                Layout.fillWidth: true; height: 5; radius: 3
                color: index < Math.max(1, meter.verdict.score) ? meter.verdict.color : Theme.line
                Behavior on color { ColorAnimation { duration: Theme.fast } }
            }
        }
    }
    Text {
        Layout.fillWidth: true; wrapMode: Text.WordWrap
        text: meter.verdict.text; color: meter.verdict.score <= 1 ? meter.verdict.color : Theme.muted
        font.family: Theme.font; font.pixelSize: 12
    }

    readonly property var common: ["password", "password1", "passw0rd", "qwertyui", "qwerty123", "qwertyuiop",
        "asdfghjk", "zxcvbnm1", "12345678", "123456789", "1234567890", "87654321", "11111111", "00000000",
        "1q2w3e4r", "1qaz2wsx", "abcd1234", "abc12345", "iloveyou", "sunshine", "princess", "football",
        "baseball", "superman", "starwars", "welcome1", "admin123", "letmein1", "trustno1", "dragon12",
        "monkey12", "michael1", "йцукенгш", "пароль12", "qwerty12", "aaaaaaaa", "q1w2e3r4", "zaq12wsx",
        "1q2w3e4r5t", "password123"]

    function rate(p) {
        var unique = {}
        for (var i = 0; i < p.length; ++i) unique[p[i]] = true
        var different = Object.keys(unique).length
        if (p.length < 8)
            return {score: 0, color: Theme.bad, text: "Ещё " + (8 - p.length) + " " + plural(8 - p.length) + " до минимума (8)"}
        if (common.indexOf(p.toLowerCase()) >= 0 || different < 4)
            return {score: 0, color: Theme.bad, text: "Слишком простой, такой подберут сразу"}
        var pool = 0
        if (/[a-z]/.test(p)) pool += 26
        if (/[A-Z]/.test(p)) pool += 26
        if (/[а-яё]/.test(p)) pool += 33
        if (/[А-ЯЁ]/.test(p)) pool += 33
        if (/[0-9]/.test(p)) pool += 10
        if (/[^a-zA-Zа-яёА-ЯЁ0-9]/.test(p)) pool += 20
        // Repeated and sequential characters (aaa, 123, abc) add almost nothing.
        var effective = 0
        for (var j = 0; j < p.length; ++j) {
            var d1 = j > 0 ? p.charCodeAt(j) - p.charCodeAt(j - 1) : 99
            effective += (Math.abs(d1) <= 1) ? 0.3 : 1
        }
        var bits = effective * Math.log(Math.max(pool, 10)) / Math.LN2
        if (bits < 36) return {score: 1, color: Theme.bad, text: "Слабый: добавьте ещё пару слов или символов"}
        if (bits < 50) return {score: 2, color: Theme.warn, text: "Средний: подойдёт, но длиннее будет надёжнее"}
        if (bits < 70) return {score: 3, color: Theme.good, text: "Хороший пароль"}
        return {score: 4, color: Theme.good, text: "Надёжный пароль"}
    }
    function plural(n) { return n === 1 ? "символ" : (n < 5 ? "символа" : "символов") }
}
