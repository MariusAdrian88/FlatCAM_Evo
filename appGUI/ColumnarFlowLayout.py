# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File by:  David Robertson (c)                            #
# Date:     5/2020                                         #
# License:  MIT Licence                                    #
# ##########################################################

import sys

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QLayout
import math


class ColumnarFlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)

        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)

        self.setSpacing(spacing)
        self.itemList = []

    def __del__(self):
        del_item = self.takeAt(0)
        while del_item:
            del_item = self.takeAt(0)

    def addItem(self, item):
        self.itemList.append(item)

    def count(self):
        return len(self.itemList)

    def itemAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self.doLayout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.doLayout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()

        for item in self.visible_items():
            size = size.expandedTo(item.minimumSize())

        left, top, right, bottom = self.getContentsMargins()

        size += QSize(left + right, top + bottom)
        return size

    def visible_items(self):
        # hidden widgets take no room in the layout
        return [item for item in self.itemList if not item.isEmpty()]

    def doLayout(self, rect: QRect, testOnly: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        area = rect.adjusted(left, top, -right, -bottom)

        spacing = self.spacing()
        if spacing < 0:
            # no spacing from the style
            spacing = 6

        items = self.visible_items()
        if not items:
            return top + bottom

        # A column has to be at least as wide as the widest minimum width. The column count uses the median
        # preferred width, so that a single wide item does not reduce the column count for all the other items.
        min_width = max(item.minimumSize().width() for item in items)
        hint_widths = sorted(item.sizeHint().width() for item in items)
        target_width = max(min_width, hint_widths[len(hint_widths) // 2])

        # Determine how many equal-width columns we can get
        column_count = math.floor((area.width() + spacing) / (target_width + spacing))
        column_count = min(column_count, len(items))
        column_count = max(1, column_count)

        while True:
            column_width = math.floor((area.width() - (column_count-1)*spacing) / column_count)
            # a column is never wider than the widest preferred width; the free space stays at the end
            column_width = max(min_width, min(column_width, hint_widths[-1]))

            # Get the heights for all of our items. The heightForWidth() of some items is too small before
            # they are shown, so the preferred height and the minimum height are a lower limit.
            item_heights = {}
            for item in items:
                height = max(item.sizeHint().height(), item.minimumSize().height())
                if item.hasHeightForWidth():
                    height = max(height, item.heightForWidth(column_width))
                item_heights[item] = height

            column_contents, column_heights = self.fill_columns(items, item_heights, column_count, spacing)

            # the items can fit in fewer columns than we have; then make the layout again with wider columns
            used_columns = sum(1 for column in column_contents if column)
            if used_columns >= column_count:
                break
            column_count = used_columns

        # Set geometry according to the layout we have calculated
        if not testOnly:
            # the first column is on the right in a right-to-left application
            parent = self.parentWidget()
            direction = parent.layoutDirection() if parent is not None else QtWidgets.QApplication.layoutDirection()
            for column_index, column_items in enumerate(column_contents):
                x = area.x() + column_index * (column_width + spacing)
                y = area.y()
                for item in column_items:
                    height = item_heights[item]
                    item.setGeometry(QtWidgets.QStyle.visualRect(direction, area, QRect(x, y, column_width, height)))
                    y += (height + spacing)

        # Return the overall height; each column height holds a spacing after the last item
        return max(column_heights) - spacing + top + bottom

    @staticmethod
    def fill_columns(items, item_heights, column_count, spacing):
        # Prepare our column representation
        column_contents = []
        column_heights = []
        for column_index in range(column_count):
            column_contents.append([])
            column_heights.append(0)

        def add_to_column(column: int, item):
            column_contents[column].append(item)
            column_heights[column] += (item_heights[item] + spacing)

        def shove_one(from_column: int) -> bool:
            if len(column_contents[from_column]) >= 1:
                item = column_contents[from_column].pop(0)
                column_heights[from_column] -= (item_heights[item] + spacing)
                add_to_column(from_column-1, item)
                return True
            return False

        def shove_cascade_consider(from_column: int) -> bool:
            changed_item = False

            if len(column_contents[from_column]) > 1:
                item = column_contents[from_column][0]
                item_height = item_heights[item]
                if column_heights[from_column-1] + item_height < max(column_heights):
                    changed_item = shove_one(from_column) or changed_item

            if from_column+1 < column_count:
                changed_item = shove_cascade_consider(from_column+1) or changed_item

            return changed_item

        def shove_cascade() -> bool:
            if column_count < 2:
                return False
            changed_item = True
            while changed_item:
                changed_item = shove_cascade_consider(1)
            return changed_item

        def pick_best_shoving_position() -> int:
            best_pos = 1
            best_height = sys.maxsize
            for column_idx in range(1, column_count):
                if len(column_contents[column_idx]) == 0:
                    continue
                item = column_contents[column_idx][0]
                height_after_shove = column_heights[column_idx-1] + item_heights[item]
                if height_after_shove < best_height:
                    best_height = height_after_shove
                    best_pos = column_idx
            return best_pos

        # Calculate the best layout
        column_index = 0
        for item in items:
            item_height = item_heights[item]
            if column_heights[column_index] != 0 and (column_heights[column_index] + item_height) > max(column_heights):
                column_index += 1
                if column_index >= column_count:
                    # Run out of room, need to shove more stuff in each column
                    if column_count >= 2:
                        changed = shove_cascade()
                        if not changed:
                            shoving_pos = pick_best_shoving_position()
                            shove_one(shoving_pos)
                            shove_cascade()
                    column_index = column_count-1

            add_to_column(column_index, item)

        shove_cascade()

        return column_contents, column_heights

    def addStretch(self, val=None):
        wdg = QtWidgets.QWidget()
        wdg.setMinimumWidth(0)
        return wdg

    def addLayout(self, layout):
        for idx in range(layout.count()):
            wdg = layout.itemAt(idx).widget()
            if wdg:
                self.addWidget(wdg)
