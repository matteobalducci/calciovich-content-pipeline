#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_shorts.py — genera i JSON di produzione di TUTTI i 20 Short.
Ogni scena ha gia' predisposti: image (illustrazione epica) + audio (voce ElevenLabs) + vo (TTS
fallback) + text (didascalia). Output in scene/shorts/shortNN.json.
Poi:  python3 make_video.py scene/shorts/short01.json
"""
import os, json
HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "scene", "shorts")
os.makedirs(OUT, exist_ok=True)

def I(name): return f"illustrazioni/{name}.png"

# scena: (illustrazione, didascalia, voce, motion)
def S(ill, text, vo, motion="in", **extra):
    d = {"image": I(ill), "text": text, "vo": vo, "motion": motion}
    d.update(extra); return d

CAT = {
"short01": dict(title="short01-il-pallone-tornava-da-solo", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-volto-giovane", None, "Tu lo conosci come Calciovich. Il più forte di sempre. Ma quella che ti hanno raccontato è solo la pubblicità.", pre="La vera storia di", big="Calciovich"),
    S("ill-01-passerotto", "A un bambino il pallone tornava da solo.", "C'è un paese tra un vulcano e un mare, dove a un bambino il pallone tornava da solo. Lo tirava, e quello tornava. Sempre.", "left"),
    S("ill-02-primo-tiro", "Una volta. Due. Alla terza, nessuno disse più niente.", "La prima volta dissero: il vento. La seconda: la pendenza. La terza, non dissero più niente.", "right"),
    S("ill-volto-1", "Un po' di paura. E un po' di meraviglia.", "Perché quando una cosa accade e non sai spiegarla, cominci ad avere un po' di paura, e un po' di meraviglia.", "up"),
]),
"short02": dict(title="short02-cose-che-non-sa-fare-1", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-06-zenit", None, "Calciovich sa fare tutto con un pallone. Tranne quattro cose.", big="Cose che NON sa fare"),
    S("ill-05-gol-impossibile", "Perdere. Sbagliare un rigore.", "Non sa perdere. Non sa sbagliare un rigore, nemmeno volendo.", "in"),
    S("ill-01-passerotto", "Passare inosservato.", "Non sa passare inosservato: la polvere rossa lo segue ovunque.", "left"),
    S("ill-abuela", "…e ricordarsi il suo vero nome.", "E la quarta: ricordarsi il suo vero nome. Lalo. Te lo racconto nella parte due.", "in"),
]),
"short03": dict(title="short03-el-vecio-dixe", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-03-profezia-vecio", None, "Un vecchio glielo disse quando aveva cinque anni. E lui rise.", big="El don xe anche na condana"),
    S("ill-03-profezia-vecio", "«Il dono è anche una condanna.»", "El don xe anche na condana. Il dono è anche una condanna. Soprattutto per chi non sbaglia mai.", "in"),
    S("ill-volto-1", "Ci avrebbe messo una vita a capirla.", "Lalo aveva cinque anni. Rise. Ci avrebbe messo una vita intera a capire cosa intendeva.", "up"),
]),
"short04": dict(title="short04-lettere-allabuela-1", intro="ill-volto-1", outro="ill-volto-1", scenes=[
    S("ill-10-telefonata", None, "L'uomo più famoso della Terra, di notte, scriveva a sua nonna.", big="Lettere all'abuela"),
    S("ill-10-telefonata", "Qui nessuno mi chiama Lalo.", "Abuela, oggi ho vinto un'altra coppa. Non ricordo nemmeno quale. Qui nessuno mi chiama Lalo.", "in"),
    S("ill-volto-1", "Tu lo sai, vero, che mi chiamo ancora Lalo?", "Tu lo sai, vero, che mi chiamo ancora Lalo? Aveva tutto. Gli mancava un nome.", "up"),
]),
"short05": dict(title="short05-il-desiderio-piu-strano", intro="ill-abuela", outro="ill-volto-1", scenes=[
    S("ill-abuela", None, "Prima che partisse, sua nonna gli fece il desiderio più strano del mondo.", big="Il desiderio dell'abuela"),
    S("ill-abuela", "«Far ridere la gente. Non farla smettere.»", "Le nonne ti augurano di essere il più bravo. Lei no. Lei disse: sii bravo abbastanza da far ridere la gente, non così bravo da farla smettere.", "in"),
    S("ill-bambino", "Ci avrebbe messo la vita a capire.", "Lui non capì. Annuì, perché si annuisce alle nonne. Ci avrebbe messo la vita a capire.", "up"),
]),
"short06": dict(title="short06-lo-sbadiglio-in-finale", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-06-zenit", None, "Una finale del mondo. Lui segna il sesto gol. E succede l'impensabile.", big="Lo sbadiglio"),
    S("ill-07-sbadiglio", "6 a 0… e uno sbadiglio.", "Esultarono al primo. Al sesto gol di un uomo solo, qualcuno fece la cosa più impensabile di tutte: si annoiò. E sbadigliò.", "in"),
    S("ill-volto-2", "L'inizio di qualcosa. Nessuno lo sentì.", "Quel piccolo sbadiglio era l'inizio di qualcosa. E nessuno, in quello stadio, lo sentì.", "up"),
]),
"short07": dict(title="short07-il-gol-in-cui-senti-la-rete", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-08-grande-silenzio", None, "Segnò il gol più bello della sua vita. E poté sentire il fruscio della rete. Sai perché?", big="Il suono mai sentito"),
    S("ill-08-grande-silenzio", "Lo stadio era vuoto.", "In tutta la storia del calcio nessuno aveva mai sentito quel suono: lo copriva il boato. Lui lo sentì. Perché lo stadio era vuoto.", "in"),
    S("ill-volto-1", "Il gol più bello. Davanti a nessuno.", "Non c'è boato più assordante di quel piccolo fruscio nel silenzio.", "up"),
]),
"short08": dict(title="short08-non-ce-piu-il-momento", intro="ill-volto-1", outro="ill-volto-1", scenes=[
    S("ill-08-grande-silenzio", None, "C'era un bambino in prima fila che, prima di ogni suo tiro, chiudeva gli occhi.", big="Non c'è più il momento"),
    S("ill-bambino", "«Era bello quel momento, prima, che non sapevi.»", "Prima, quando tiravi, io chiudevo gli occhi. Perché magari non entrava. Adesso entra sempre. Non c'è più il momento.", "in"),
    S("ill-volto-2", "Gli aveva tolto il forse.", "Lo disse un bambino di otto anni. E a Calciovich entrò dritto nel petto.", "up"),
]),
"short09": dict(title="short09-provo-a-sbagliare", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-05-gol-impossibile", None, "Per salvare il calcio, decise di giocare male. Apposta. Fu un disastro.", big="Sbagliare apposta"),
    S("ill-05-gol-impossibile", "Mirò fuori. E si infilò all'incrocio.", "Mirò fuori, apposta. La palla girò in aria e si infilò all'incrocio. I suoi piedi non lo tradivano mai.", "in"),
    S("ill-volto-2", "La sbagliosità non si finge.", "Aspettò il rumore bello del pubblico. Non arrivò. Perché un errore finto non è un forse. La sbagliosità non si può fingere.", "up"),
]),
"short10": dict(title="short10-non-ha-mai-esultato", intro="ill-volto-1", outro="ill-volto-1", scenes=[
    S("ill-05-gol-impossibile", None, "Ha segnato più gol di chiunque nella storia. E non ha mai esultato. Nemmeno una volta.", big="Mai un'esultanza"),
    S("ill-volto-1", "Non sapeva cosa fare delle braccia.", "Gli altri correvano sotto la curva, urlavano. Lui no. Dopo il gol non sapeva mai cosa fare delle braccia.", "in"),
    S("ill-volto-2", "Sapeva una cosa che noi avremmo capito tardi.", "Perché c'era già qualcosa, dentro di lui, che sapeva una cosa che il mondo avrebbe capito troppo tardi.", "up"),
]),
"short11": dict(title="short11-trentadue-secondi", intro="ill-volto-giovane", outro="ill-volto-1", scenes=[
    S("ill-02-primo-tiro", None, "Bastarono trentadue secondi a far innamorare il mondo intero.", big="32 secondi"),
    S("ill-02-primo-tiro", "Guardate questo.", "Un cugino filmò trentadue secondi e scrisse sotto due parole: guardate questo. Lo guardarono milioni di volte in un giorno.", "in"),
    S("ill-granchio", "E poi arrivarono le macchine nere.", "Allora sembrava solo meraviglia. E dopo i numeri, arrivarono le macchine nere.", "up"),
]),
"short12": dict(title="short12-sei-un-mercato", intro="ill-granchio", outro="ill-volto-1", scenes=[
    S("ill-granchio", None, "Il giorno in cui un uomo gli disse cos'era diventato davvero.", big="Sei un mercato"),
    S("ill-granchio", "«Tu non sei un ragazzo. Sei un mercato.»", "Camminava di sbieco, il Granchio. Gli strinse la mano e non gliela ridiede più: ragazzo, tu non sei un ragazzo, tu sei un mercato.", "in"),
    S("ill-volto-2", "Dopo la fama, arriva il prezzo.", "Il Vecio sputò il sigaro: la fama non è il problema. Il problema è che dopo la fama arriva il prezzo.", "up"),
]),
"short13": dict(title="short13-cose-che-non-sa-fare-2", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-10-telefonata", None, "Mancava la quarta cosa che non sa fare: ricordarsi il suo vero nome.", big="Il suo vero nome"),
    S("ill-10-telefonata", "Il telefono squillò nel vuoto.", "Una sera, da solo, ebbe bisogno di sentirsi chiamare in un altro modo. Chiamò casa. Il telefono squillò nel vuoto.", "in"),
    S("ill-abuela", "C'era un nome che non diceva più nessuno: Lalo.", "Perché c'era un nome piccolo, vero, che adesso non diceva più nessuno: Lalo.", "up"),
]),
"short14": dict(title="short14-vendettero-laria", intro="ill-cartellone", outro="ill-volto-1", scenes=[
    S("ill-cartellone", None, "Misero il suo nome ovunque. Alla fine vendettero perfino l'aria.", big="Vendettero l'aria"),
    S("ill-cartellone", "SII LEGGENDA. A rate.", "La maglia, le scarpe, l'acqua ai parametri dell'atleta, il materasso per sognare di essere lui. E un cartellone: sii leggenda. A rate.", "in"),
    S("ill-volto-2", "Comprava il forse di essere lui.", "Perché la gente non comprava le scarpe. Comprava il forse di essere, per un attimo, lui.", "up"),
]),
"short15": dict(title="short15-una-moglie-in-ogni-citta", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-volto-2", None, "Dicevano avesse una moglie in ogni città. Ed era l'uomo più solo del mondo.", big="Soledad"),
    S("ill-10-telefonata", "Soledad. Solitudine.", "Una di loro si chiamava Soledad. Solitudine. È l'unica della lista che diceva la verità.", "in"),
    S("ill-volto-1", "Nessuna lo chiamava Lalo.", "Perché nessuna di loro, mai, lo chiamava Lalo.", "up"),
]),
"short16": dict(title="short16-non-ricordo-il-paese", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-10-telefonata", None, "Una notte si svegliò e non ricordava in che paese del mondo si trovasse.", big="Chi sono?"),
    S("ill-volto-2", "Provò a dirsi il proprio nome. E gli venne «Calciovich».", "Provò a fare la cosa dei bambini quando hanno paura: dirsi il proprio nome. E gli venne Calciovich. E si spaventò.", "in"),
    S("ill-volto-1", "Il marchio si mangiava l'uomo.", "Perché per un istante non si era ricordato di chiamarsi anche in un altro modo.", "up"),
]),
"short17": dict(title="short17-disse-due-parole", intro="ill-volto-2", outro="ill-volto-1", scenes=[
    S("ill-07-sbadiglio", None, "Un telecronista disse due parole in diretta. Lo licenziarono il giorno dopo.", big="…è troppo"),
    S("ill-07-sbadiglio", "Non «è scarso». «È troppo».", "Stava commentando l'ennesima partita a senso unico. E invece di gridare, tacque. Poi disse piano: è troppo.", "in"),
    S("ill-08-grande-silenzio", "E gli stadi cominciarono a svuotarsi.", "Lo licenziarono. Ma ormai l'aveva detto. Per tutti. E gli stadi, piano, cominciarono a svuotarsi.", "up"),
]),
"short18": dict(title="short18-la-signora-grigia", intro="ill-09-noiona", outro="ill-volto-1", scenes=[
    S("ill-09-noiona", None, "Negli stadi cominciò a comparire una signora grigia. Nessuno sapeva chi fosse.", big="La Noiona"),
    S("ill-09-noiona", "Dove si sedeva, la gente smetteva di gridare.", "Arrivava a partita iniziata. Si sedeva. E sbadigliava. E dovunque si sedeva, la gente smetteva di gridare.", "in"),
    S("ill-volto-2", "E perché lo guardava con pena?", "La chiamarono la Noiona. E guardava Calciovich in un modo strano. Con pena. Perché?", "up"),
]),
"short19": dict(title="short19-vinse-tutto", intro="ill-06-zenit", outro="ill-volto-1", scenes=[
    S("ill-06-zenit", None, "Vinse l'ultima coppa che restava. Ed è lì che cominciò la fine.", big="In cima, il niente"),
    S("ill-11-realizzazione", "In vetta c'era il niente.", "Aveva svuotato la vetrina del mondo. Ma in cima non c'era niente: quando hai vinto tutto, non hai più niente da volere.", "in"),
    S("ill-volto-2", "Desiderò di poter perdere.", "Provò a desiderare una cosa, una sola. E desiderò, ridicolo, di poter ancora perdere.", "up"),
]),
"short20": dict(title="short20-lettere-allabuela-2", intro="ill-volto-1", outro="ill-12-ritorno", scenes=[
    S("ill-abuela", None, "Seconda lettera a una nonna lontana.", big="Lettere all'abuela II"),
    S("ill-volto-1", "«Non così bravo da farla smettere.»", "Abuela, mi avevi detto: sii bravo abbastanza da far ridere la gente, non così bravo da farla smettere. Adesso comincio a capirla.", "in"),
    S("ill-12-ritorno", "E comincio ad avere paura di saperlo.", "Tu lo sai cosa volevi dire, vero? Perché io comincio ad avere paura di saperlo.", "up"),
]),
}

def build():
    n = 0
    for key, v in CAT.items():
        scenes = []
        for i, sc in enumerate(v["scenes"], start=1):
            sc = dict(sc); sc["audio"] = f"audio/{key}_{i}.mp3"
            scenes.append(sc)
        doc = {"title": v["title"], "format": "vertical", "fps": 30, "voice": "Alice",
               "intro": True, "intro_image": I(v["intro"]),
               "outro": True, "outro_image": I(v["outro"]),
               "scenes": scenes}
        with open(os.path.join(OUT, key + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        n += 1
    print(f"Generati {n} JSON in {OUT}")

if __name__ == "__main__":
    build()
