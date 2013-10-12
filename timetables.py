from flask import Flask, render_template, request, abort
from flask.ext.sqlalchemy import SQLAlchemy
from flask_wtf import Form
from wtforms import SelectMultipleField, SelectField
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm import subqueryload
from collections import defaultdict
import datetime

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///test.db"
app.config["WTF_CSRF_ENABLED"] = False
app.config["ANALYTICS_ID"] = "UA-7457807-9"
db = SQLAlchemy(app)


START_HOUR = datetime.time(hour=9, minute=15)
END_HOUR = datetime.time(hour=17, minute=45)
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def dt(time):
    return datetime.datetime.combine(datetime.date.today(), time)


class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True)
    modules = db.relationship("Module", cascade="all,delete-orphan", backref=db.backref("department", lazy="joined"))


class Module(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True)
    name = db.Column(db.String(100))
    week_start = db.Column(db.Integer)
    week_end = db.Column(db.Integer)

    dept_id = db.Column(db.Integer, db.ForeignKey('department.id'))
    lectures = db.relationship("Lecture", backref=db.backref("module", lazy="joined"))


class Lecture(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    activity = db.Column(db.String(50))
    day = db.Column(db.String(10))
    start = db.Column(db.Time)
    end = db.Column(db.Time)

    room = db.Column(db.String(50))
    staff = db.Column(db.String(1024))

    module_id = db.Column(db.Integer, db.ForeignKey("module.id"))
    weeks = db.relation("LectureWeek", backref=db.backref("lecture", lazy="joined"))

    def __repr__(self):
        return "<Lesson %s on %s:%s>" % (self.activity, self.day, self.start)

    @staticmethod
    def _total_seconds(td):
        # Keep backward compatibility with Python 2.6 which doesn't have
        # this method
        if hasattr(td, 'total_seconds'):
            return td.total_seconds()
        else:
            return (td.microseconds + (td.seconds + td.days * 24 * 3600) * 10**6) / 10**6

    def col_span(self, increment):
        return int(self._total_seconds(dt(self.end) - dt(self.start))
                   / self._total_seconds(increment))


class LectureWeek(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lecture.id"))

    week_start = db.Column(db.Integer)
    week_end = db.Column(db.Integer)

    def __str__(self):
        return "%s%s" % (self.week_start, "-%s" % self.week_end if self.week_end != self.week_start else "")

    def __repr__(self):
        return "<LessonWeek: %s-%s for %s>" % (self.week_start, self.week_end, self.lesson_id)


class WeekDay(object):
    def __init__(self, increment):
        self.times = list(column_generator(increment))
        self.lessons = defaultdict(list)
        self.increment = increment

        self._day_rows = [
            WeekDayRow(len(self.times), self.increment)
        ]
        self._lesson_count = 0
        self._rows = None

    def flatten_rows(self):
        for idx, x in enumerate(self.times):
            lessons = [item for item in (o.get_column(idx)
                                         for o in self._day_rows)
                       if item not in (1, None)]

            if lessons:
                yield x.strftime("%H:%M"), lessons

    def get_lesson_count(self):
        return sum(
            x.lecture_count for x in self.get_rows()
        )

    def add_lesson(self, lesson):
        self.lessons[lesson.start].append(lesson)

    def get_rows(self):
        if self._rows is None:
            self._rows = self._get_rows()
            return self._rows

        return self._rows

    def _get_rows(self):
        for idx, time in enumerate(self.times):
            classes = self.lessons.get(time, [])
            for klass in classes:
                for row in self._day_rows:
                    if row.add_lesson(idx, klass):
                        break
                else:
                    # Add a new result
                    new_row = WeekDayRow(len(self.times), self.increment)
                    new_row.add_lesson(idx, klass)
                    self._day_rows.append(new_row)

        return self._day_rows

    def has_lessons(self):
        return any(
            any(x.columns) for x in self.get_rows()
        )


class WeekDayRow(object):
    def __init__(self, increment_count, increment):
        self.increment_count = increment_count
        self.columns = [None for x in xrange(increment_count)]
        self.increment = increment
        self.lecture_count = 0

    def add_lesson(self, idx, lesson):
        if self.columns[idx] is None:
            lesson_span = lesson.col_span(self.increment)
            self.columns[idx] = lesson
            if lesson_span > 1:
                # Fill up the other timeslots
                self.columns[idx+1:idx+lesson_span] = [1 for _ in xrange(lesson_span - 1)]
            self.lecture_count += 1
            return True
        else:
            return False

    def get_elements(self):
        return self.columns

    def get_column(self, idx):
        return self.columns[idx]


class ColourManager(object):
    MAX_COLOURS = 6

    def __init__(self):
        self._seen_lectures = {}
        self._colours = [
            "lightblue", "lightcoral", "lightgreen", "lightsalmon", "lightseagreen", "lightsteelblue"
        ]

    def get_color(self, id):
        colour = self._seen_lectures.get(id, None)

        if colour:
            return colour

        self._seen_lectures[id] = self._colours.pop()
        return self._seen_lectures[id]


class NullColourManager(ColourManager):
    def get_color(self, id):
        return "lightgrey"


class TimeTableForm(Form):
    department = SelectField()
    modules = SelectMultipleField()


def column_generator(increment):
    global START_HOUR, END_HOUR

    start = dt(START_HOUR) - increment
    end = dt(END_HOUR)
    while start < end:
        start = start + increment
        yield start.time()


@app.route("/timetable")
def timetable():
    module_codes = request.args.get("modules", "").split(",")
    modules = Module.query.filter(Module.code.in_(module_codes)).filter(LectureWeek.week_start > 20) .all()

    increment = datetime.timedelta(minutes=30)

    for module in modules:
        for lecture in module.lectures:
            lesson_length = (dt(lecture.end) - dt(lecture.start))
            if lesson_length < increment:
                # The lesson length is too small for the increment, decrease the increment size
                increment /= 2

    module_dict = defaultdict(lambda: WeekDay(increment))
    for module in modules:
        for klass in module.lectures:
            module_dict[klass.day].add_lesson(klass)

    column_generated = list(column_generator(increment))

    if len(modules) > ColourManager.MAX_COLOURS:
        colour_manager = NullColourManager()
    else:
        colour_manager = ColourManager()

    try:
        current_day = DAYS[datetime.date.today().weekday()]
    except IndexError:
        current_day = None

    return render_template("timetable_page.html", modules=modules,
                           columns=column_generated, days=DAYS,
                           module_dict=module_dict, increment=increment,
                           current_day=current_day,
                           colours=colour_manager)

@app.route('/', methods=("GET", "POST"))
def index():
    form = TimeTableForm()
    form.department.choices = [(o.name, o.name) for o in Department.query.all()]

    if request.args.get("department", None):
        try:
            dept = Department.query.filter_by(name=request.args["department"]).one()
            form.modules.choices = [(o.code, "%s %s" % (o.code, o.name or "")) for o in dept.modules]
        except NoResultFound:
            return abort(404)

    #form.modules.choices = [(str(o.id), o.code) for o in Module.query.all()]

    return render_template("landing_page.html", form=form)


if __name__ == '__main__':
    db.create_all()
    app.run()
