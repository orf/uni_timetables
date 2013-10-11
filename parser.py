from collections import namedtuple
import bs4
import timetables
import datetime
import os

Module = namedtuple("Module", "code weeks name")
Lecture = namedtuple("Lesson", "activity start end weeks room staff")


def import_into_db(modules_dict, dept_name):
    dept = timetables.Department(name=dept_name)
    timetables.db.session.add(dept)

    for module, days_dict in modules_dict.items():
        week_start, week_end = module.weeks.split("-")
        module_db = timetables.Module(code=module.code, department=dept, name=module.name,
                                      week_start=week_start, week_end=week_end)
        timetables.db.session.add(module_db)

        for day, lessons in days_dict.items():
            for lesson in lessons:
                staff = lesson.staff if not lesson.staff.isspace() else None
                if staff is not None:
                    staff = ", ".join(staff.split(","))
                lesson_db = timetables.Lecture(activity=lesson.activity,
                                               start=datetime.datetime.strptime(lesson.start, "%H:%M").time(),
                                               end=datetime.datetime.strptime(lesson.end, "%H:%M").time(),
                                               day=day,
                                               room=lesson.room,
                                               staff=staff,
                                               module=module_db)
                timetables.db.session.add(lesson_db)

                if "," in lesson.weeks:
                    lesson_weeks = [l.split("-") for l in lesson.weeks.split(",")]
                else:
                    lesson_weeks = [lesson.weeks.split("-")]

                for week_group in lesson_weeks:
                    if len(week_group) == 1:
                        week_start, week_end = week_group[0], week_group[0]
                    else:
                        week_start, week_end = week_group

                    lesson_week = timetables.LectureWeek(lecture=lesson_db, week_start=week_start, week_end=week_end)
                    timetables.db.session.add(lesson_week)

        timetables.db.session.commit()

        if not module_db.lectures:
            timetables.db.session.delete(module_db)

    timetables.db.session.commit()


def parse_module_header(node):
    """
    I take a module header table and return a Module object
    """
    code_table = node.find("table", attrs={"class": "header-0-args"})
    module_code = code_table.find("span", attrs={"header-0-0-0"}).text

    week_table = node.find("table", attrs={"class": "header-1-args"})
    module_weeks = week_table.find("span", attrs={"class": "header-1-2-1"}).text

    return Module(code=module_code, weeks=module_weeks, name=None)


def parse_spreadsheet_row(node):
    # Activity, Description, Start, End, Weeks, Room, Staff
    # No neat way to parse this.
    td_nodes = node.findAll("td")
    return (Lecture(
        activity=td_nodes[0].text,
        start=td_nodes[2].text,
        end=td_nodes[3].text,
        weeks=td_nodes[4].text,
        room=td_nodes[5].text,
        staff=td_nodes[6].text
    ), td_nodes[1].text)


def parse(html):
    soup = bs4.BeautifulSoup(html)

    current_module = None
    current_day = None
    current_returner = {}
    returner = {}

    for node in soup.find("body").children:
        if isinstance(node, bs4.NavigableString):
            continue

        node_class = node.attrs.get("class", [])

        if "header-border-args" in node_class:
            # New module
            if current_module is not None:
                returner[current_module] = current_returner

            current_module = parse_module_header(node)
            current_returner = {}
            current_day = None

        elif node.name == "p":
            # New day
            day_node = node.find("span", attrs={"class": "labelone"})
            current_day = day_node.text

        elif "spreadsheet" in node_class:
            # We have a timetable, parse it
            table_body_nodes = (item for item in node.findAll("tr")
                                if not "class" in item.attrs)

            results = []
            for n in table_body_nodes:
                res, module_desc = parse_spreadsheet_row(n)
                current_module = current_module._replace(name=module_desc)
                results.append(res)

            if results:
                current_returner[current_day] = results

    return returner

if __name__ == "__main__":
    timetables.db.create_all()

    # Doesn't cascade when using sqlite
    #timetables.Department.query.delete()

    # So we do it this way instead...
    for t in timetables.Department.query.all():
        timetables.db.session.delete(t)
    timetables.db.session.commit()


    for name in os.listdir("to_parse"):
        with open(os.path.join("to_parse", name), "rb") as fd:
            parsed = parse(fd.read())
            #pprint.pprint(parsed)
            print "Parsed file, importing %s into database" % os.path.splitext(name)[0]
            import_into_db(parsed, os.path.splitext(name)[0])
            print "Finished"