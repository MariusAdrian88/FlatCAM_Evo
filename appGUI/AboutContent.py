"""Credits and legal text shared by Help > About and the startup welcome."""
import builtins
import gettext

if '_' not in builtins.__dict__:
    _ = gettext.gettext


REPOSITORY = 'https://github.com/MariusAdrian88/FlatCAM_Evo'
WIKI = REPOSITORY + '/wiki'

PROGRAMMERS = (
    'Denis Hayrullin', 'Kamil Sopko', 'David Robertson', 'Matthieu Berthomé', 'Mike Evans',
    'Victor Benso', 'Jørn Sandvik Nilsson', 'Lei Zheng', 'Leandro Heck', 'Marco A Quezada',
    'Cedric Dussud', 'Chris Hemingway', 'David Kahler', 'Damian Wrobel', 'Daniel Sallin',
    'Bruno Vunderl', 'Gonzalo Lopez', 'Jakob Staudt', 'Mike Smith', 'Barnaby Walters',
    'Steve Martina', 'Thomas Duffin', 'Andrey Kultyapov', 'Alex Lazar', 'Chris Breneman',
    'Eric Varsanyi', 'Lubos Medovarsky', '@Idechix', '@SM', '@grbf', '@Symonty', '@mgix',
    'Emily Ellis', 'Maksym Stetsyuk', 'Peter Nitschneider', 'Bogusz Jagoda', 'Andre Spahlinger',
    'Hans Boot', 'Dmitriy Klabukov', 'Robert Niemöller', 'Adam Coddington', 'Ali Khalil',
    'Maftei Albert-Alexandru', '@gargamel778', '@ErkanGench', '@michaelosthege',
)

TRANSLATORS = (
    {'language': 'BR - Portuguese', 'authors': [('Carlos Stein', '<carlos.stein@gmail.com>')]},
    {'language': 'Chinese Simplified', 'authors': [('余俊潇 (Yu Junxiao)', '')]},
    {'language': 'French', 'authors': [('Michel Maciejewski', '<micmac589@gmail.com>'), ('Olivier Cornet', '')]},
    {'language': 'Italian', 'authors': [('Massimiliano Golfetto', '<golfetto.pcb@gmail.com>')]},
    {'language': 'German', 'authors': [('Marius Stanciu (Google-Tr)', ''), ('Jens Karstedt', ''), ('Detlef Eckardt', '')]},
    {'language': 'Romanian', 'authors': [('Marius Stanciu', '<marius_adrian@yahoo.com>')]},
    {'language': 'Russian', 'authors': [('Andrey Kultyapov', '<camellan@yandex.ru>')]},
    {'language': 'Spanish', 'authors': [('Marius Stanciu (Google-Tr)', '')]},
    {'language': 'Turkish', 'authors': [('Mehmet Kaya', '<malatyakaya480@gmail.com>')]},
)


def license_text():
    return _(
        'Permission is hereby granted, free of charge, to any person obtaining a copy\n'
        'of this software and associated documentation files (the "Software"), to deal\n'
        'in the Software without restriction, including without limitation the rights\n'
        'to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n'
        'copies of the Software, and to permit persons to whom the Software is\n'
        'furnished to do so, subject to the following conditions:\n\n'
        'The above copyright notice and this permission notice shall be included in\n'
        'all copies or substantial portions of the Software.\n\n'
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
        'IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n'
        'FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n'
        'AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n'
        'LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n'
        'OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN\n'
        'THE SOFTWARE.'
    )


def attributions_text():
    return _(
        'Some of the icons used are from the following sources:<br>'
        '<div>Icons by <a href="https://www.flaticon.com/authors/freepik" '
        'title="Freepik">Freepik</a> from <a href="https://www.flaticon.com/"             '
        'title="Flaticon">www.flaticon.com</a></div>'
        '<div>Icons by <a target="_blank" href="https://icons8.com">Icons8</a></div>'
        'Icons by <a href="http://www.onlinewebfonts.com">oNline Web Fonts</a>'
        '<div>Icons by <a href="https://www.flaticon.com/authors/pixel-perfect" '
        'title="Pixel perfect">Pixel perfect</a> from <a href="https://www.flaticon.com/" '
        'title="Flaticon">www.flaticon.com</a></div>'
        '<div>Icons by <a href="https://www.flaticon.com/authors/anggara" '
        'title="Anggara"> Anggara </a> from <a href="https://www.flaticon.com/" '
        'title="Flaticon">www.flaticon.com</a></div>'
        '<div>Icons by <a href="https://www.flaticon.com/authors/kharisma" '
        'title="Kharisma"> Kharisma </a> from <a href="https://www.flaticon.com/" '
        'title="Flaticon">www.flaticon.com</a></div>'
    )
