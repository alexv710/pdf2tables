// Ensure namespace for aggrid component is ready
if (!window.dashAgGridComponentFunctions) {
    window.dashAgGridComponentFunctions = {};
}


window.dashAgGridComponentFunctions.CustomTooltip = function (props) {
    const htmlString = props.value;
    const lines = htmlString.split('<br>');

    const elements = lines.map((line, index) => {
        return React.createElement('div', {
            key: index,
            dangerouslySetInnerHTML: { __html: line }
        });
    });

    return React.createElement(
        'div',
        {
            style: {
                backgroundColor: '#F8F8F8',
                border: '1px solid #d3d3d3',
                borderRadius: '4px',
                boxShadow: '0 4px 8px rgba(0, 0, 0, 0.1)',
                padding: '10px',
                color: 'black',
            }
        },
        elements
    );
};